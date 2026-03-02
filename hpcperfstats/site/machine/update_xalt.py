#!/usr/bin/env python
import os
import sys

# Append your local repository path here:
# sys.path.append("/home/sg99/hpcperfstats")
from datetime import datetime, timedelta

os.environ['DJANGO_SETTINGS_MODULE']='hpcperfstats.site.hpcperfstats_site.settings'
import django

django.setup()
from hpcperfstats.site.machine.models import Job, Libraries
from hpcperfstats.site.xalt.models import join_run_object, lib, run

try:
    start = datetime.strptime(sys.argv[1],"%Y-%m-%d")
    try:
        end   = datetime.strptime(sys.argv[2],"%Y-%m-%d")
    except:
        end = start
except:
    start = datetime.now()
    end   = datetime.now()

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)

for date in daterange(start, end):
    directory = date.strftime("%Y-%m-%d")
    print (directory)
    ### If xalt is available add data to the DB
    #for job in Job.objects.filter(date = directory).filter(exe = None):
    for job in Job.objects.filter(date = directory).filter(exe = None):
        obj = Job.objects.get(id = job.id)
        xd = None

        if not run.objects.using('xalt').filter(job_id = job.id): continue
        for r in run.objects.using('xalt').filter(job_id = job.id):
            if "usr" in r.exec_path.split('/'): continue
            print (r.exec_path)
            xd = r
        if not xd: continue
        obj.exe  = xd.exec_path.split('/')[-1][0:128]
        obj.exec_path = xd.exec_path
        obj.cwd     = xd.cwd[0:128]
        obj.threads = xd.num_threads
        obj.save()
        for join in join_run_object.objects.using('xalt').filter(run_id = xd.run_id):
            object_path = lib.objects.using('xalt').get(obj_id = join.obj_id).object_path
            module_name = lib.objects.using('xalt').get(obj_id = join.obj_id).module_name
            if not module_name: module_name = 'none'
            library = Libraries(object_path = object_path, module_name = module_name)
            library.save()
            library.jobs.add(obj)
