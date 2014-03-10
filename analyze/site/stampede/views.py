from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response, render
from django.views.generic import DetailView, ListView
import matplotlib, string
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pylab import figure, hist, plot

from stampede.models import Job, JobForm
import sys_path_append
import os,sys
import analysis
from analysis.gen import tspl, lariat_utils
from analysis.plot import plots as plt
import job_stats as data
import cPickle as pickle 
import time
   
import numpy as np

from django.core.cache import cache,get_cache 

path = sys_path_append.pickles_dir

def update(meta = None):
    if not meta: return

    #Job.objects.all().delete()

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
        json['cwd'] = ld.cwd[0:128]
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
    return render_to_response("stampede/dates.html", { 'date_list' : date_list})

def search(request):

    if 'q' in request.GET:
        q = request.GET['q']
        try:
            job = Job.objects.get(id = q)
            return HttpResponseRedirect("/stampede/job/"+str(job.id)+"/")
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

    return render(request, 'stampede/dates.html', {'error' : True})


def index(request, date = None, uid = None, project = None, user = None, exe = None):

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
        field['exe__contains'] = exe
    
    field['run_time__gte'] = 60 

    job_list = Job.objects.filter(**field).order_by('-id')
    field['job_list'] = job_list
    field['nj'] = len(job_list)

    return render_to_response("stampede/index.html", field)

def hist_summary(request, date = None, uid = None, project = None, user = None, exe = None):

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
        field['exe__contains'] = exe
    
    field['run_time__gte'] = 60 
    field['status'] = 'COMPLETED'

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

    return figure_to_response(fig)


def figure_to_response(f):
    response = HttpResponse(content_type='image/png')
    f.savefig(response, format='png')
    return response

def get_data(pk):
    if cache.has_key(pk):
        data = cache.get(pk)
    else:
        job = Job.objects.get(pk = pk)
        with open(job.path,'rb') as f:
            data = pickle.load(f)
            cache.set(job.id, data)
    return data

def master_plot(request, pk):
    data = get_data(pk)
    mp = plt.MasterPlot()
    fig = mp.plot(pk,ld="pass",mintime=60,job_data=data)
    return figure_to_response(fig)

def heat_map(request, pk):
    
    data = get_data(pk)
    hm = plt.HeatMap(['intel_snb','intel_snb'],
                     ['CLOCKS_UNHALTED_REF',
                      'INSTRUCTIONS_RETIRED'])
    fig = hm.plot(pk,ld="pass",job_data=data)
    return figure_to_response(fig)

class JobDetailView(DetailView):

    model = Job
    
    def get_context_data(self, **kwargs):
        context = super(JobDetailView, self).get_context_data(**kwargs)
        job = context['job']

        data = get_data(job.id)

        type_list = []
        host_list = []

        for host_name, host in data.hosts.iteritems():
            host_list.append(host_name)
        for type_name, type in host.stats.iteritems():
            schema = data.get_schema(type_name).desc
            schema = string.replace(schema,',E',' ')
            schema = string.replace(schema,',',' ')
            type_list.append( (type_name, schema) )

        type_list = sorted(type_list, key = lambda type_name: type_name[0])
        context['type_list'] = type_list
        context['host_list'] = host_list

        return context

def type_plot(request, pk, type_name):
    data = get_data(pk)

    schema = data.get_schema(type_name).desc
    schema = string.replace(schema,',E',' ')
    schema = string.replace(schema,' ,',',').split()
    schema = [x.split(',')[0] for x in schema]

    k1 = {'intel_snb' : [type_name]*len(schema)}
    k2 = {'intel_snb': schema}

    tp = plt.DevPlot(k1,k2)

    fig=tp.plot(pk,ld='pass',job_data=data)

    return figure_to_response(fig)


def type_detail(request, pk, type_name):
    data = get_data(pk)

    schema = data.get_schema(type_name).desc
    schema = string.replace(schema,',E',' ')
    schema = string.replace(schema,' ,',',').split()

    raw_stats = data.aggregate_stats(type_name)[0]  

    stats = []
    scale = 1.0
    if type_name == 'mem': scale=1.0/2**10.
    for t in range(len(raw_stats)):
        temp = []
        for event in range(len(raw_stats[t])):
            temp.append(raw_stats[t,event]*scale)
        stats.append((data.times[t],temp))


    return render_to_response("stampede/type_detail.html",{"type_name" : type_name, "jobid" : pk, "stats_data" : stats, "schema" : schema})
    
