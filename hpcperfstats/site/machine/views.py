"""Django views for machine app: home, search, index (job list + histograms), job detail (SummaryPlot, XALT, Lustre), type_detail (DevPlot), host_detail, admin_monitor. Uses OAuth2 and jid_table/plot providers.

"""
import hpcperfstats.conf_parser as cfg

openblas_threads = int(cfg.get_total_cores()) / 4
if openblas_threads < 1:
  openblas_threads = 1

from datetime import timedelta, timezone as dt_utc
from math import ceil
import hashlib
import os

os.environ['OPENBLAS_NUM_THREADS'] = str(openblas_threads)

import time

from bokeh.embed import components
from bokeh.layouts import gridplot
from bokeh.models import HoverTool
from bokeh.plotting import figure
from django import forms
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Max
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import DetailView
from numpy import histogram, isnan, linspace, log
from pandas import DataFrame, to_timedelta

import hpcperfstats.analysis.plot as plots
from hpcperfstats.analysis.gen import jid_table
from hpcperfstats.analysis.gen.jid_table import HostDataProvider, TypeDetailDataProvider
from hpcperfstats.analysis.gen.utils import clean_dataframe
from hpcperfstats.site.machine.cache_utils import (
    KEY_DATES,
    KEY_METRICS_DISTINCT,
    KEY_QUEUES,
    KEY_STATES,
    KEY_ALL_HOSTS,
    KEY_HOST_LAST,
    KEY_GPU_QS,
    KEY_XALT,
    KEY_JOB,
    KEY_TYPE_DETAIL_HOSTS,
    cached_orm,
    TIMEOUT_MEDIUM,
    TIMEOUT_SHORT,
    TIMEOUT_LONG,
)
from hpcperfstats.site.machine.models import host_data, job_data, metrics_data
from hpcperfstats.site.machine.oauth2 import check_for_tokens
from hpcperfstats.site.xalt.models import join_run_object, lib, run

local_timezone = cfg.get_timezone()


class DataNotFoundException(Exception):
  """Raised when no job data matches the search (e.g. index filter returns empty).

    """
  pass


class libset_c:
  """Simple container for (object_path, module_name) used in XALT libset.

    """

  def __init__(self, object_path, module_name):
    """Store object_path and module_name.

        """
    self.module_name = module_name
    self.object_path = object_path


class xalt_data_c:
  """Container for XALT data: exec_path list, cwd list, libset list.

    """

  def __init__(self):
    """Initialize empty exec_path, cwd, and libset lists.

        """
    self.exec_path = []
    self.cwd = []
    self.libset = []


def home(request, error=False):
  """Render search page with date list and metrics; redirect to login if not authenticated.

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  field = {}
  month_dict = {}

  def _dates_fn():
    return sorted(job_data.objects.dates("end_time", "day"))

  date_list = cached_orm(KEY_DATES, TIMEOUT_MEDIUM, _dates_fn)
  if date_list:
    for date in date_list:
      y, m, d = str(date.year), str(date.month), str(date.day)
      month_dict.setdefault(y + "-" + m, [])
      month_dict[y + "-" + m].append((str(date), d))

  field["machine_name"] = cfg.get_host_name_ext()
  field["date_list"] = sorted(month_dict.items())[::-1]
  field["error"] = error

  def _metrics_fn():
    return list(
        metrics_data.objects.distinct("metric").values("metric", "units"))

  field["metrics"] = cached_orm(KEY_METRICS_DISTINCT, TIMEOUT_LONG, _metrics_fn)

  field["choice"] = ChoiceForm()
  return render(request, "machine/search.html", field)


def search(request):
  """Dispatch by GET: jid -> job detail redirect; host -> host_detail; else index. On failure return home with error.

    """
  if "jid" in request.GET:
    try:
      jid = request.GET["jid"]
      job_jid = cached_orm(
          f"{KEY_JOB}:{jid}",
          TIMEOUT_SHORT,
          lambda: job_data.objects.filter(jid=jid).values_list("jid", flat=True).first(),
      )
      if job_jid:
        return HttpResponseRedirect("/machine/job/" + str(job_jid) + "/")
      messages.error(request, "No result found in search")
    except Exception:
      messages.error(request, "No result found in search")
  elif "host" in request.GET and request.GET["host"]:
    try:
      print("try to get host")
      return host_detail(request)
    except:
      messages.error(request, "No result found in search")
      pass
  else:
    try:
      return index(request)
    except:
      messages.error(request, "No result found in search")
      pass

  return home(request, error=True)


def index(request, **kwargs):
  """Filter jobs by GET params, build metric histograms, paginate, and render index template with script/div for Bokeh.

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  fields = request.GET.dict()
  fields = {k: v for k, v in fields.items() if v}
  fields.update(kwargs)
  print(fields)

  ### Filter
  # Build query and filter on job accounting data
  acct_data = {
      k: v
      for k, v in fields.items()
      if k.split('_', 1)[0] != "metrics" and k != "page"
  }
  job_list = job_data.objects.filter(**acct_data).order_by('-end_time')

  # Build query and filter iteratively on derived metrics data
  df_fields = []
  cur_metrics = {
      k.split('_', 1)[1]: v
      for k, v in fields.items()
      if k.split('_', 1)[0] == "metrics"
  }
  for key, val in cur_metrics.items():
    name, op = key.split('__')
    mquery = {"metrics_data__metric": name, "metrics_data__value__" + op: val}
    job_list = job_list.filter(**mquery)
    df_fields += [name]
  fields['nj'] = job_list.count()
  df_fields = list(set(df_fields))

  if not len(job_list):
    raise DataNotFoundException("No data found for this search request")

  acc_cols = ["jid", "start_time", "submit_time", "runtime", "nhosts"]
  job_rows = list(job_list.values(*acc_cols))
  jids_ordered = [r["jid"] for r in job_rows]
  job_df = DataFrame(job_rows).set_index("jid")

  metrics_rows = list(
      metrics_data.objects.filter(jid_id__in=jids_ordered).values(
          "jid_id", "metric", "units", "value"
      ))
  metric_dict = {}
  hist_metrics_set = set()
  for row in metrics_rows:
    metric_dict.setdefault(row["metric"], []).append((row["jid_id"], row["value"]))
    hist_metrics_set.add((row["metric"], row["units"]))

  jid_dict = {"jid": jids_ordered}
  for name in df_fields:
    jid_to_val = {jid: val for jid, val in metric_dict.get(name, [])}
    jid_dict[name] = [jid_to_val.get(j, None) for j in jids_ordered]
  df = DataFrame(jid_dict).set_index("jid")
  hist_metrics = list(hist_metrics_set)
  df = df.join(job_df)

  # Base fields to use in histograms added to derived metrics explicitly searched on
  hist_metrics += [("runtime", "hours"), ("nhosts", "#nodes"),
                   ("queue_wait", "hours")]
  df["queue_wait"] = to_timedelta(df["start_time"] -
                                  df["submit_time"]).dt.total_seconds() / 3600
  df["runtime"] = df["runtime"] / 3600.

  df = clean_dataframe(df)

  ###

  ### Pagination
  paginator = Paginator(job_list, min(100, len(job_list)))
  page_num = request.GET.get('page')

  try:
    jobs = paginator.page(page_num)
  except PageNotAnInteger:
    jobs = paginator.page(1)
  except EmptyPage:
    jobs = paginator.page(paginator.num_pages)

  fields['job_list'] = jobs
  ###

  ### Build Histogram Plots
  plots = []
  for metric, label in hist_metrics:
    plots += [job_hist(df, metric, label)]
  fields["script"], fields["div"] = components(gridplot(plots, ncols=2))
  ###

  fields['logged_in'] = True
  if '?' in request.get_full_path():
    fields['current_path'] = request.get_full_path()

  return render(request, "machine/index.html", fields)


# Generate Histogram Plots of a List of Metrics
def job_hist(df, metric, label):
  """Build a Bokeh quad histogram for the given metric column and axis label.

    """
  hover = HoverTool(tooltips=[("jobs", "@top"), ("bin", "[@left, @right]")],
                    point_policy="snap_to_data")
  TOOLS = ["pan,wheel_zoom,box_zoom,reset,save,box_select", hover]

  values = list(df[metric].values)
  if len(values) == 0:
    return None

  hist, edges = histogram(values,
                          bins=linspace(0, max(values),
                                        max(3, int(5 * log(len(values))))))

  plot = figure(title=metric,
                toolbar_location=None,
                height=400,
                width=600,
                y_range=(1, max(hist)),
                tools=TOOLS)  #  y_axis_type = "log",
  plot.xaxis.axis_label = label
  plot.yaxis.axis_label = "# jobs"

  plot.quad(top=hist, bottom=1, left=edges[:-1], right=edges[1:])

  return plot


def heat_map(pk):
  """Return Bokeh components for HeatMap plot for job pk (legacy; get_data may not be defined in this module).

    """
  data = get_data(pk)
  hm = plots.HeatMap()
  return components(hm.plot(data))


class job_dataDetailView(DetailView):
  """Django DetailView for a single job_data: summary plot, Lustre/XALT context, Splunk URLs. Non-staff users see only their jobs.

    """
  model = job_data

  def get_queryset(self):
    """Restrict to current user's jobs unless is_staff.

        """
    queryset = super(job_dataDetailView, self).get_queryset()
    if "is_staff" in self.request.session and self.request.session["is_staff"]:
      return queryset
    return queryset.filter(username=self.request.session["username"])

  def get(self, request, *args, **kwargs):
    """Redirect to login if not authenticated.

        """
    if not check_for_tokens(self.request):
      return HttpResponseRedirect("/login_prompt")

    return super().get(request, *args, **kwargs)

  def get_context_data(self, **kwargs):
    """Add host_list, summary plot (mscript/mdiv), Lustre fsio, schema, XALT data, Splunk URLs.

        """
    context = super(job_dataDetailView, self).get_context_data(**kwargs)
    job = context['job_data']

    j = jid_table.jid_table(job.jid)

    context["host_list"] = j.acct_host_list

    # gpu (ORM, cached)
    try:
      gpu_list = cached_orm(
          f"{KEY_GPU_QS}:{job.jid}",
          TIMEOUT_SHORT,
          lambda: list(
              host_data.objects.filter(
                  jid=job.jid,
                  type="nvidia_gpu",
                  event="utilization",
              ).values("type", "event", "value").order_by("time")),
      )
      gpu_data = DataFrame(gpu_list) if gpu_list else DataFrame()
      if not gpu_data.empty and len(gpu_data) > 2:
        gpu_data = gpu_data.iloc[1:-1]
        gpu_utilization_max = gpu_data['value'].max()
        gpu_utilization_mean = gpu_data['value'].mean()
        if not isnan(gpu_utilization_max):
          context["gpu_active"] = ceil(gpu_utilization_max / 100.0)
          context["gpu_utilization_max"] = gpu_utilization_max
          context["gpu_utilization_mean"] = gpu_utilization_mean
    except Exception as e:
      print("error getting gpu data:", e)

    # xalt (cached)
    if cfg.get_xalt_user() != "":

      def _xalt_fn():
        xalt_data = xalt_data_c()
        for r in run.objects.using("xalt").filter(job_id=job.jid).only(
            "exec_path", "cwd", "run_id"):
          if "usr" in r.exec_path.split("/"):
            continue
          xalt_data.exec_path.append(r.exec_path)
          xalt_data.cwd.append(r.cwd[0:128])
          for join in join_run_object.objects.using("xalt").filter(
              run_id=r.run_id):
            obj = lib.objects.using("xalt").only(
                "object_path", "module_name").get(obj_id=join.obj_id)
            module_name = obj.module_name or "none"
            if any(libtmp.module_name == module_name
                   for libtmp in xalt_data.libset):
              continue
            xalt_data.libset.append(
                libset_c(object_path=obj.object_path, module_name=module_name))
        xalt_data.exec_path = list(set(xalt_data.exec_path))
        xalt_data.cwd = list(set(xalt_data.cwd))
        xalt_data.libset = sorted(xalt_data.libset, key=lambda x: x.module_name)
        return {
            "exec_path":
                xalt_data.exec_path,
            "cwd":
                xalt_data.cwd,
            "libset": [(l.object_path, l.module_name) for l in xalt_data.libset
                       ],
        }

      xalt_payload = cached_orm(f"{KEY_XALT}:{job.jid}", TIMEOUT_SHORT,
                                _xalt_fn)
      if xalt_payload:
        xalt_data = xalt_data_c()
        xalt_data.exec_path = xalt_payload["exec_path"]
        xalt_data.cwd = xalt_payload["cwd"]
        xalt_data.libset = [
            libset_c(object_path=o, module_name=m)
            for o, m in xalt_payload["libset"]
        ]
        context["xalt_data"] = xalt_data
      else:
        context["xalt_data"] = xalt_data_c()
    else:
      context["xalt_data"] = []

    # Build Summary Plot
    ptime = time.time()
    sp = plots.SummaryPlot(j)
    #try:
    context["mscript"], context["mdiv"] = components(sp.plot())
    #except:
    #    print("failed to generate summary plot for jid {0}".format(j.jid))
    print("plot time: {0:.1f}".format(time.time() - ptime))

    # Compute Lustre Usage (ORM)
    try:
      llite_df = j.get_llite_delta_by_event()
      if not llite_df.empty and "delta_sum" in llite_df.columns:
        llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
        read_row = llite_df[llite_df["event"] == "read_bytes"]
        write_row = llite_df[llite_df["event"] == "write_bytes"]
        read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
        write_val = float(
            write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
        context['fsio'] = {"llite": [read_val, write_val]}
    except Exception as e:
      print("failed to compute Lustre data movement for jid {0}: {1}".format(
          j.jid, e))
    try:
      context["schema"] = j.schema
    except:
      print("failed to extract schema for jid {0}".format(j.jid))

    ### Specific to TACC Splunk
    urlstring = "https://scribe.tacc.utexas.edu/en-US/app/search/search?q=search%20"
    hoststring = urlstring + "%20host%3D" + j.acct_host_list[
        0] + cfg.get_host_name_ext()
    serverstring = urlstring + "%20mds*%20OR%20%20oss*"
    for host in j.acct_host_list[1:]:
      hoststring += "%20OR%20%20host%3D" + host + "*"

    hoststring += "&earliest=" + str(j.start_time) + "&latest=" + str(
        j.end_time) + "&display.prefs.events.count=50"
    serverstring += "&earliest=" + str(j.start_time) + "&latest=" + str(
        j.end_time) + "&display.prefs.events.count=50"
    context['client_url'] = hoststring
    context['server_url'] = serverstring
    ###
    context['logged_in'] = True

    return context


def type_detail(request, jid, type_name):
  """Render type-detail page with DevPlot for the given job and type (e.g. llite, cpu). Uses TypeDetailDataProvider.

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  # Job accounting via ORM (cached)
  job = cached_orm(
      f"{KEY_JOB}:{jid}",
      TIMEOUT_SHORT,
      lambda: job_data.objects.filter(jid=jid)
      .only("host_list", "start_time", "end_time")
      .first(),
  )
  if not job:
    messages.error(request, "Job not found.")
    return HttpResponseRedirect("/")

  acct_host_list = [
      h + '.' + cfg.get_host_name_ext() for h in (job.host_list or [])
  ]
  start_time = job.start_time
  end_time = job.end_time
  if start_time.tzinfo is None:
    start_time = timezone.make_aware(start_time, dt_utc.utc)
  if end_time.tzinfo is None:
    end_time = timezone.make_aware(end_time, dt_utc.utc)
  start_time = start_time.astimezone(local_timezone)
  end_time = end_time.astimezone(local_timezone)

  provider = TypeDetailDataProvider(jid, type_name, start_time, end_time,
                                    acct_host_list)

  def _type_hosts_fn():
    return list(
        host_data.objects.filter(
            jid=jid,
            type=type_name,
            time__gte=start_time,
            time__lte=end_time,
            host__in=acct_host_list,
        ).values_list("host", flat=True).distinct())

  _st = start_time.isoformat() if start_time else ""
  _et = end_time.isoformat() if end_time else ""
  data_host_list = cached_orm(
      f"{KEY_TYPE_DETAIL_HOSTS}:{jid}:{type_name}:{_st}:{_et}",
      TIMEOUT_SHORT,
      _type_hosts_fn,
  )
  if len(data_host_list) == 0:
    return render(
        request, "machine/type_detail.html", {
            "type_name": type_name,
            "jobid": jid,
            "tscript": "",
            "tdiv": "",
            "logged_in": True,
            "stats_data": [],
            "schema": []
        })

  ptime = time.time()
  sp = plots.DevPlot(provider, data_host_list)
  df, plot = sp.plot()
  script, div = components(plot)
  schema = [c for c in df.columns if c not in ("host", "time", "index")
            ] if not df.empty else []

  if not df.empty and "time" in df.columns and len(df) > 0 and schema:
    df = df.copy()
    df['dt'] = df['time'].sub(df['time'].iloc[0]).astype('timedelta64[s]')
    df1 = df.groupby('dt')[schema].mean().reset_index()
    stats = [(df1['dt'].iloc[t], df1.loc[df1.index[t],
                                         schema].values.flatten().tolist())
             for t in range(len(df1))]
  else:
    stats = []

  print("type plot time: {0:.1f}".format(time.time() - ptime))
  return render(
      request, "machine/type_detail.html", {
          "type_name": type_name,
          "jobid": jid,
          "tscript": script,
          "tdiv": div,
          "logged_in": True,
          "stats_data": stats,
          "schema": schema
      })


def host_detail(request):
  """Render summary plot for a single host and time range (GET host, end_time__gte, end_time__lte). Uses HostDataProvider.

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  fields = request.GET.dict()
  fields = {k: v for k, v in fields.items() if v}
  start_time = fields.get('end_time__gte')
  end_time = fields.get('end_time__lte', 'now()')
  host_fqdn = fields.get('host')
  if not host_fqdn or not start_time:
    messages.error(request, "Missing host or time range.")
    return HttpResponseRedirect("/")

  # Parse times; support "now()" for end
  from datetime import datetime
  try:
    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
  except (ValueError, TypeError):
    start_dt = timezone.now() - timedelta(days=1)
  if end_time == "now()" or not end_time:
    end_dt = timezone.now()
  else:
    try:
      end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
      end_dt = timezone.now()
  if start_dt.tzinfo is None:
    start_dt = timezone.make_aware(start_dt)
  if end_dt.tzinfo is None:
    end_dt = timezone.make_aware(end_dt)

  ht = HostDataProvider(host_fqdn, start_dt, end_dt)

  ptime = time.time()
  sp = plots.SummaryPlot(ht)
  script, div = components(sp.plot())
  print("plot time: {0:.1f}".format(time.time() - ptime))

  return render(
      request, "machine/type_detail.html", {
          "type_name": fields['host'],
          "tag": fields['host'],
          "tscript": script,
          "tdiv": div,
          "logged_in": True
      })


def proc_detail(request, pk, proc_name):
  """Render process detail for a job and process name (VmPeak, VmHWM, etc.). Uses get_data(pk) legacy path.

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  data = get_data(pk)

  host_map = {}
  schema = data.get_schema('proc')
  vmp_idx = schema['VmPeak'].index
  hwm_idx = schema['VmHWM'].index
  hwm_unit = "gB"
  thr_idx = schema['Threads'].index

  for host_name, host in data.hosts.items():
    for proc_pid, val in host.stats['proc'].items():

      host_map.setdefault(host_name, {})
      proc_, pid, cpu_aff, mem_aff = proc_pid.split('/')

      if proc_ == proc_name:
        host_map[host_name][proc_ + '/' + pid] = [
            val[-1][vmp_idx] / 2**20, val[-1][hwm_idx] / 2**20, cpu_aff,
            val[-1][thr_idx]
        ]

  return render(
      request, "machine/proc_detail.html", {
          "proc_name": proc_name,
          "jobid": pk,
          "host_map": host_map,
          "hwm_unit": hwm_unit,
          "logged_in": True
      })


def admin_monitor(request):
  """Staff-only view: list all hosts with last host_data sample time and age bucket (ok, gt_10min, gt_hour, etc.).

    """
  if not check_for_tokens(request):
    return HttpResponseRedirect("/login_prompt")

  # Restrict to staff users if that flag is present on the session
  if not request.session.get("is_staff", False):
    return HttpResponseRedirect("/")

  def _all_hosts_fn():
    qs = job_data.objects.distinct("host_list").values_list("host_list",
                                                            flat=True)
    flat = [host for sublist in qs for host in sublist]
    return list(set(flat))

  all_hosts = cached_orm(KEY_ALL_HOSTS, TIMEOUT_MEDIUM, _all_hosts_fn)

  now = timezone.now()
  time_bounds = now - timedelta(days=8)
  _tb = time_bounds.isoformat()

  host_stats = []
  for host in all_hosts:

    def _host_last_fn(h=host, tb=_tb):
      return (
          host_data.objects.filter(
              host__icontains=h, time__gte=time_bounds
          )
          .order_by("-time")
          .values_list("time", flat=True)
          .first()
      )

    last_time = cached_orm(
        f"{KEY_HOST_LAST}:{host}:{_tb}",
        TIMEOUT_SHORT,
        _host_last_fn,
    )
    if last_time is None:
      host_stats.append({
          "host": host,
          "last_time": None,
          "age_bucket": "gt_week"
      })
      continue
    else:
      age = now - last_time
      if age > timedelta(weeks=1):
        bucket = "gt_week"
      elif age > timedelta(days=1):
        bucket = "gt_day"
      elif age > timedelta(hours=1):
        bucket = "gt_hour"
      elif age > timedelta(minutes=10):
        bucket = "gt_10min"
      else:
        bucket = "ok"
    host_stats.append({
        "host": host,
        "last_time": last_time,
        "age_bucket": bucket
    })

  context = {
      "host_stats": host_stats,
      "logged_in": True,
  }
  return render(request, "machine/admin_monitor.html", context)


class ChoiceForm(forms.Form):
  """Form with queue and state dropdowns populated from job_data (cached). Used on search page.

    """
  queue = forms.ChoiceField(choices=[], widget=forms.Select())
  state = forms.ChoiceField(choices=[], widget=forms.Select())

  def __init__(self, *args, **kwargs):
    """Populate queue and state choices from cached job_data."""
    super().__init__(*args, **kwargs)
    try:
      queues = cached_orm(
          KEY_QUEUES,
          TIMEOUT_MEDIUM,
          lambda: list(
              job_data.objects.distinct("queue").values_list("queue", flat=True)
          ),
      )
      states = cached_orm(
          KEY_STATES,
          TIMEOUT_MEDIUM,
          lambda: list(
              job_data.objects.exclude(state__contains="CANCELLED by").distinct(
                  "state").values_list("state", flat=True)),
      )
      self.fields["queue"].choices = [("", "")
                                      ] + [(q, q) for q in (queues or [])]
      self.fields["state"].choices = [("", "")
                                      ] + [(s, s) for s in (states or [])]
    except Exception as e:
      print(e)
      print("Continuing in case of makemigrations")
