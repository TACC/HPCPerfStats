#!/usr/bin/env python
import os, sys, pwd, time
from datetime import timedelta, datetime
import psycopg
import multiprocessing

os.environ['DJANGO_SETTINGS_MODULE']='hpcperfstats.site.hpcperfstats_site.settings'
import django
django.setup()

from hpcperfstats.site.machine.models import job_data, metrics_data
from hpcperfstats.analysis.metrics import metrics

import hpcperfstats.conf_parser as cfg
from hpcperfstats.progress import progress

thread_count = 20

def update_metrics(date, rerun = False):

    min_time = 300
    jobs_list = list(job_data.objects.filter(end_time__date = date.date()).exclude(runtime__lt = min_time))
    print("Total jobs {0}".format(len(jobs_list)) + " for date " + date.strftime("%Y-%m-%d"))

    if not rerun:
        jobs_list = [job for job in jobs_list if not job.metrics_data_set.all().exists() or job.metrics_data_set.all().filter(value__isnull = True).count() > 0]

    # Set up metric computation manager
    metrics_manager = metrics.Metrics()

    print("Compute for following metrics for date {0} on {1} jobs".format(date, len(jobs_list)))
    for name in metrics_manager.metrics_list:
        print(name)        
    
    metrics_manager.run(jobs_list)


if __name__ == "__main__":

    #################################################################
    try:
        startdate = datetime.strptime(sys.argv[1], "%Y-%m-%d")
    except: 
        startdate = datetime.combine(datetime.today(), datetime.min.time())
    try:
        enddate   = datetime.strptime(sys.argv[2], "%Y-%m-%d")
    except:
        enddate = startdate

    print("###Date Range of metrics to update: {0} -> {1}####".format(startdate, enddate))
    #################################################################

    date = startdate
    all_dates = []
    while date <= enddate:
        all_dates.append(date)
        date += timedelta(days=1)

    print(all_dates)
    with multiprocessing.Pool(processes=thread_count) as pool:
        for result in pool.imap_unordered(update_metrics, all_dates):
            print(result)


   #update_metrics(date, rerun = False)

    time.sleep(3600)
