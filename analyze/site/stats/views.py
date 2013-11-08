from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response, render
from django.views.generic import DetailView, ListView
import matplotlib, string
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pylab import figure, hist, plot

from stats.models import Job, JobForm
import sys_path_append
import os,sys
import masterplot as mp
import plotkey, tspl, lariat_utils
import job_stats as data
import MetaData
import cPickle as pickle 
import time
   
import numpy as np
 

path = sys_path_append.pickles_dir


def update():
    #Job.objects.all().delete()
    for date in os.listdir(path):
        print 'Date',date

        meta = MetaData.MetaData(os.path.join(path,date))    
        meta.load_update()

        # Only need to populate lariat cache once
        jobid = meta.json.keys()[0]
        ld = lariat_utils.LariatData(jobid,
                                     end_epoch = meta.json[jobid]['end_epoch'],
                                     directory = sys_path_append.lariat_path,
                                     daysback = 2)
        
        for jobid, json in meta.json.iteritems():
            if Job.objects.filter(id = jobid).exists(): continue  
            ld = lariat_utils.LariatData(jobid,
                                         olddata = ld.ld)
            json['user'] = ld.user
            json['exe'] = ld.exc.split('/')[-1]
            json['cwd'] = ld.cwd
            json['run_time'] = meta.json[jobid]['end_epoch'] - meta.json[jobid]['start_epoch']
            json['threads'] = ld.threads
            try:
                job_model, created = Job.objects.get_or_create(**json) 
            except:
                print "Something wrong with json",json
    return 

def dates(request):
    date_list = os.listdir(path)
    date_list = sorted(date_list, key=lambda d: map(int, d.split('-')))

    month_dict ={}

    for date in date_list:
        y,m,d = date.split('-')
        key = y+' / '+m
        if key not in month_dict: month_dict[key] = []
        date_pair = (date, d)
        month_dict[key].append(date_pair)

    date_list = month_dict
    return render_to_response("stats/dates.html", { 'date_list' : date_list})

def search(request):

    if 'q' in request.GET:
        q = request.GET['q']
        try:
            job = Job.objects.get(id = q)
            return HttpResponseRedirect("/stats/job/"+str(job.id)+"/")
        except: pass

    if 'u' in request.GET:
        u = request.GET['u']
        try:
            return index(request, uid = u)
        except: pass

    if 'n' in request.GET:
        user = request.GET['n']
        try:
            return index(request, user = user)
        except: pass

    if 'p' in request.GET:
        project = request.GET['p']
        try:
            return index(request, project = project)
        except: pass

    if 'x' in request.GET:
        x = request.GET['x']
        try:
            return index(request, exe = x)
        except: pass

    return render(request, 'stats/dates.html', {'error' : True})


def index(request, date = None, uid = None, project = None, user = None, exe = None):
    start = time.clock()
    field = {}
    if date:
        field['date'] = date
    if uid:
        field['uid'] = uid
    if user:
        field['user'] = user
    if project:
        field['project'] = project
    if exe:
        field['exe'] = exe

    if exe:
        job_list = Job.objects.filter(exe__contains=exe).filter(run_time__gte=60).order_by('-id')
    else:
        job_list = Job.objects.filter(**field).filter(run_time__gte=60).order_by('-id')
    field['job_list'] = job_list
    field['nj'] = len(job_list)
    print "index =",time.clock()-start
    return render_to_response("stats/index.html", field)

def hist_summary(request, date = None, uid = None, project = None, user = None, exe = None):

    start = time.clock()
    field = {}
    if date:
        field['date'] = date
    if uid:
        field['uid'] = uid
    if user:
        field['user'] = user
    if project:
        field['project'] = project
    if exe:
        field['exe'] = exe
        
    field['status'] = 'COMPLETED'

    if exe:
        job_list = Job.objects.filter(exe__contains=exe)
    else:
        job_list = Job.objects.filter(**field)

    fig = figure(figsize=(16,6))

    # Run times
    job_times = np.array([job.run_time for job in job_list])/3600.
    ax = fig.add_subplot(121)
    ax.hist(job_times, max(5, 5*np.log(len(job_list))))
    ax.set_xlim((0,max(job_times)+1))
    ax.set_ylabel('# of jobs')
    ax.set_xlabel('# hrs')
    ax.set_title('Run Times for Completed Jobs')

    # Number of cores
    job_size = [job.cores for job in job_list]
    ax = fig.add_subplot(122)
    ax.hist(job_size, max(5, 5*np.log(len(job_list))))
    ax.set_xlim((0,max(job_size)+1))
    ax.set_title('Run Sizes for Completed Jobs')
    ax.set_xlabel('# cores')

    #fig.tight_layout()
    print "hist =", time.clock()-start
    return figure_to_response(fig)

def stats_load(job):
    with open(job.path) as f:
        job.stats = pickle.load(f)
    job.save()

def get_schema(job, type_name):
    with open(job.path) as f:
        data = pickle.load(f)
    schema = data.get_schema(type_name).desc
    schema = string.replace(schema,',E',' ')
    schema = string.replace(schema,' ,',',').split()
    schema = [x.split(',')[0] for x in schema]

    return schema


def figure_to_response(f):
    response = HttpResponse(content_type='image/svg+xml')
    f.savefig(response, format='svg')
    #response = HttpResponse(content_type='image/png')
    #f.savefig(response, format='png')
    plt.close(f)
    f.clear()
    return response

def stats_unload(job):
    job.stats = []
    job.save()

def master_plot(request, pk):
    job = Job.objects.get(id = pk)
    fig, fname = mp.master_plot(job.path,header=None,mintime=60)
    return figure_to_response(fig)

def heat_map(request, pk):
    job = Job.objects.get(id = pk)
    k1 = {'intel_snb' : ['intel_snb']}

    k2 = {'intel_snb': ['INSTRUCTIONS_RETIRED']}
    ts0 = tspl.TSPLBase(job.path,k1,k2)

    k2 = {'intel_snb': ['CLOCKS_UNHALTED_CORE']}
    ts1 = tspl.TSPLBase(job.path,k1,k2)



    cpi = np.array([])
    hosts = []
    for v in ts0.data[0]:
        hosts.append(v)
        ncores = len(ts0.data[0][v])
        for k in range(ncores):
            i = np.array(ts0.data[0][v][k],dtype=np.float)
            c = np.array(ts1.data[0][v][k],dtype=np.float)
            ratio = np.divide(np.diff(i),np.diff(c))
            if not cpi.size: cpi = np.array([ratio])
            else: cpi = np.vstack((cpi,ratio))
    cpi_min, cpi_max = cpi.min(), cpi.max()

    fig,ax=plt.subplots(1,1,figsize=(8,12),dpi=110)

    ycore = np.arange(cpi.shape[0]+1)
    time = ts0.t/3600.

    yhost=np.arange(len(hosts)+1)*ncores + ncores    
    for l in range(len(yhost)):
        plt.axhline(y=yhost[l], color='black', lw=2, linestyle='--', rasterized=True)

    plt.yticks(yhost - ncores/2.,hosts,size='small')
    plt.axis([time.min(),time.max(),ycore.min(),ycore.max()])

    plt.pcolor(time, ycore, cpi, vmin=cpi_min, vmax=cpi_max)
    plt.title('Instructions Retired per Core Clock Cycle')
    plt.clim(cpi_min,cpi_max)
    plt.colorbar()

    ax.set_xlabel('Time (hrs)')

    plt.close()

    return figure_to_response(fig)

class JobDetailView(DetailView):

    model = Job
    
    def get_context_data(self, **kwargs):
        context = super(JobDetailView, self).get_context_data(**kwargs)
        job = context['job']
        stats_load(job)
        type_list = []
        host_list = []
        for host_name, host in job.stats.hosts.iteritems():
            host_list.append(host_name)
        for type_name, type in host.stats.iteritems():
            schema = job.stats.get_schema(type_name).desc
            schema = string.replace(schema,',E',' ')
            schema = string.replace(schema,',',' ')
            type_list.append( (type_name, schema) )

        type_list = sorted(type_list, key = lambda type_name: type_name[0])
        context['type_list'] = type_list
        context['host_list'] = host_list
        stats_unload(job)
        return context

def type_plot(request, pk, type_name):

    job = Job.objects.get(id = pk)
    schema = get_schema(job, type_name)

    k1 = {'intel_snb' : [type_name]*len(schema)}
    k2 = {'intel_snb': schema}

    ts = tspl.TSPLSum(job.path,k1,k2)
    
    nr_events = len(schema)
    fig, axarr = plt.subplots(nr_events, sharex=True, figsize=(8,nr_events*2), dpi=80)
    do_rate = True
    for i in range(nr_events):
        if type_name == 'mem': do_rate = False

        mp.plot_lines(axarr[i], ts, [i], 3600., do_rate = do_rate)
        axarr[i].set_ylabel(schema[i],size='small')
    axarr[-1].set_xlabel("Time (hr)")
    fig.subplots_adjust(hspace=0.0)
    fig.tight_layout()

    return figure_to_response(fig)


def type_detail(request, pk, type_name):

    job = Job.objects.get(id = pk)
    stats_load(job)
    data = job.stats

    schema = data.get_schema(type_name).desc
    schema = string.replace(schema,',E',' ')
    schema = string.replace(schema,' ,',',').split()

    raw_stats = data.aggregate_stats(type_name)[0]  

    stats = []
    for t in range(len(raw_stats)):
        temp = []
        for event in range(len(raw_stats[t])):
            temp.append(raw_stats[t,event])
        stats.append((data.times[t],temp))

    stats_unload(job)

    return render_to_response("stats/type_detail.html",{"type_name" : type_name, "jobid" : pk, "stats_data" : stats, "schema" : schema})
    
