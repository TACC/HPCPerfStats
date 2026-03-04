#!/usr/bin/env python
import multiprocessing
import os
os.environ['DJANGO_SETTINGS_MODULE']='hpcperfstats.site.hpcperfstats_site.settings'

import sys
import time
from datetime import datetime, timedelta

import django
django.setup()

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.site.machine.models import job_data

DEBUG =  cfg.get_debug()



thread_count = 2

def update_metrics(date, rerun = False):

    min_time = 300
    jobs_list = list(job_data.objects.filter(end_time__date = date.date()).exclude(runtime__lt = min_time))
    print("Total jobs {0}".format(len(jobs_list)) + " for date " + date.strftime("%Y-%m-%d"))

    if not rerun:
        jobs_list = [job for job in jobs_list if not job.metrics_data_set.all().exists() or job.metrics_data_set.all().filter(value__isnull = True).count() > 0]

    if DEBUG:
        print("jobs that don't have data before run:")
        print(jobs_list)
    # Set up metric computation manager
    metrics_manager = metrics.Metrics()

    print("Compute for following metrics for date {0} on {1} jobs".format(date, len(jobs_list)))
    for name in metrics_manager.simple_metrics_list:
        print(name)
    for name in metrics_manager.complex_metrics_list:
        print(name)

    metrics_manager.run(jobs_list)


    if DEBUG:
        jobs_list = list(job_data.objects.filter(end_time__date = date.date()).exclude(runtime__lt = min_time))
        jobs_list = [job for job in jobs_list if not job.metrics_data_set.all().exists() or job.metrics_data_set.all().filter(value__isnull = True).count() > 0]
        print("jobs that don't have data after run:")
        print(jobs_list)


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
    for result in map(update_metrics, all_dates):
        print(result)

   #update_metrics(date, rerun = False)

    time.sleep(3600)
