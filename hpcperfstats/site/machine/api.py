"""Django REST Framework API views for machine app. All data via JSON for React SPA."""
import hpcperfstats.conf_parser as cfg
from bokeh.embed import components
from bokeh.layouts import gridplot
from django.utils import timezone
from pandas import DataFrame, to_timedelta
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .cache_utils import (
    KEY_DATES,
    KEY_METRICS_DISTINCT,
    KEY_QUEUES,
    KEY_STATES,
    KEY_ALL_HOSTS,
    KEY_HOST_LAST,
    KEY_GPU_QS,
    KEY_XALT,
    KEY_TYPE_DETAIL_HOSTS,
    KEY_JOB,
    cached_orm,
    TIMEOUT_MEDIUM,
    TIMEOUT_SHORT,
    TIMEOUT_LONG,
)
from .models import host_data, job_data, metrics_data
from .oauth2 import check_for_tokens
from .serializers import JobListSerializer
from .views import (
    job_hist,
    local_timezone,
    libset_c,
    xalt_data_c,
)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from datetime import timedelta
from numpy import isnan
from math import ceil

import hpcperfstats.analysis.gen.jid_table as jid_table
import hpcperfstats.analysis.plot as plots
from hpcperfstats.analysis.gen.utils import clean_dataframe
from hpcperfstats.site.xalt.models import join_run_object, lib, run


def _require_auth(request):
    """Return 401 JSON if not authenticated."""
    if not check_for_tokens(request):
        return Response(
            {"detail": "Authentication required", "login_url": "/login_prompt"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return None


@api_view(["GET"])
def session_info(request):
    """Return current session state for SPA (logged_in, username, is_staff)."""
    err = _require_auth(request)
    if err is not None:
        return err
    return Response({
        "logged_in": True,
        "username": request.session.get("username", ""),
        "is_staff": request.session.get("is_staff", False),
        "machine_name": cfg.get_host_name_ext(),
    })


@api_view(["GET"])
def home_options(request):
    """Return options for search form: date_list, metrics, queues, states, machine_name."""
    err = _require_auth(request)
    if err is not None:
        return err

    def _dates_fn():
        jdf = DataFrame(job_data.objects.values("end_time"))
        return jdf["end_time"].dt.date.drop_duplicates().sort_values().tolist()

    date_list = cached_orm(KEY_DATES, TIMEOUT_MEDIUM, _dates_fn)
    month_dict = {}
    if date_list:
        for d in date_list:
            y, m, day = str(d.year), str(d.month), str(d.day)
            key = f"{y}-{m}"
            if key not in month_dict:
                month_dict[key] = []
            month_dict[key].append((str(d), day))

    def _metrics_fn():
        return list(
            metrics_data.objects.distinct("metric").values("metric", "units")
        )

    metrics = cached_orm(KEY_METRICS_DISTINCT, TIMEOUT_LONG, _metrics_fn)
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
            job_data.objects.exclude(state__contains="CANCELLED by")
            .distinct("state")
            .values_list("state", flat=True)
        ),
    )

    return Response({
        "machine_name": cfg.get_host_name_ext(),
        "date_list": sorted(month_dict.items(), reverse=True),
        "metrics": list(metrics) if metrics else [],
        "queues": [q for q in (queues or []) if q],
        "states": [s for s in (states or []) if s],
    })


@api_view(["GET"])
def search_dispatch(request):
    """
    Dispatch search: if jid -> return redirect url; if host+times -> return host plot data; else return job list (index).
    """
    err = _require_auth(request)
    if err is not None:
        return err

    if request.GET.get("jid"):
        jid = request.GET["jid"]
        job = cached_orm(
            f"{KEY_JOB}:{jid}",
            TIMEOUT_SHORT,
            lambda: job_data.objects.filter(jid=jid).first(),
        )
        if job:
            return Response({"redirect": f"/machine/job/{job.jid}/"})
        return Response(
            {"error": "No result found in search"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.GET.get("host"):
        # Delegate to host_detail logic; return JSON for host plot
        from .views import host_detail
        from django.http import HttpResponse
        # We need to return type_detail-like JSON (tscript, tdiv) for host
        resp = host_detail(request)
        if resp.status_code == 302:
            return Response({"redirect": resp.url}, status=status.HTTP_302_FOUND)
        # If it's HTML we can't easily return; for API we build the same context
        return job_list(request)

    return job_list(request)


@api_view(["GET"])
def job_list(request):
    """Paginated job list with Bokeh script/div for histograms (same logic as index view)."""
    err = _require_auth(request)
    if err is not None:
        return err

    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics" and k != "page"
    }
    job_list_qs = job_data.objects.filter(**acct_data).order_by("-end_time")

    cur_metrics = {
        k.split("_", 1)[1]: v
        for k, v in fields.items()
        if k.split("_", 1)[0] == "metrics"
    }
    for key, val in cur_metrics.items():
        name, op = key.split("__")
        mquery = {
            "metrics_data__metric": name,
            "metrics_data__value__" + op: val,
        }
        job_list_qs = job_list_qs.filter(**mquery)

    nj = job_list_qs.count()
    df_fields = list(set(name for name, _ in (key.split("__") for key in cur_metrics)))

    if nj == 0:
        return Response(
            {"error": "No data found for this search request"},
            status=status.HTTP_404_NOT_FOUND,
        )

    metric_dict = {}
    jid_dict = {"jid": []}
    hist_metrics = []
    for job in job_list_qs:
        jid_dict["jid"].append(job.jid)
        for name in df_fields:
            metric_set = job.metrics_data_set.all().filter(metric=name)
            if metric_set:
                hist_metrics.append((name, metric_set[0].units))
            for m in metric_set:
                metric_dict.setdefault(m.metric, []).append(m.value)
    jid_dict.update(metric_dict)
    df = DataFrame(jid_dict).set_index("jid")

    hist_metrics = list(set(hist_metrics))
    acc_cols = ["jid", "start_time", "submit_time", "runtime", "nhosts"]
    df = df.join(
        DataFrame(job_list_qs.values(*acc_cols)).set_index("jid")
    )
    hist_metrics += [("runtime", "hours"), ("nhosts", "#nodes"), ("queue_wait", "hours")]
    df["queue_wait"] = (
        to_timedelta(df["start_time"] - df["submit_time"]).dt.total_seconds() / 3600
    )
    df["runtime"] = df["runtime"] / 3600
    df = clean_dataframe(df)

    page_num = request.GET.get("page", 1)
    paginator = Paginator(job_list_qs, min(100, nj))
    try:
        page = paginator.page(page_num)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    script = ""
    div = ""
    try:
        plot_list = [job_hist(df, metric, label) for metric, label in hist_metrics]
        plot_list = [p for p in plot_list if p is not None]
        if plot_list:
            script, div = components(gridplot(plot_list, ncols=2))
    except Exception as e:
        pass

    current_path = request.get_full_path() if "?" in request.get_full_path() else None
    qname = "Jobs"

    return Response({
        "job_list": JobListSerializer(page.object_list, many=True).data,
        "nj": nj,
        "script": script,
        "div": div,
        "current_path": current_path,
        "qname": qname,
        "pagination": {
            "page": page.number,
            "num_pages": paginator.num_pages,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": page.previous_page_number() if page.has_previous() else None,
            "next_page_number": page.next_page_number() if page.has_next() else None,
        },
    })


@api_view(["GET"])
def job_detail(request, pk):
    """Single job detail: metadata, host_list, fsio, xalt, Bokeh mscript/mdiv, schema, URLs."""
    err = _require_auth(request)
    if err is not None:
        return err

    job = cached_orm(
        f"{KEY_JOB}:{pk}",
        TIMEOUT_SHORT,
        lambda: job_data.objects.filter(jid=pk).first(),
    )
    if not job:
        return Response(
            {"error": "Job not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not request.session.get("is_staff", False):
        if job.username != request.session.get("username"):
            return Response(
                {"error": "Not allowed to view this job"},
                status=status.HTTP_403_FORBIDDEN,
            )

    j = jid_table.jid_table(job.jid)
    host_list = j.acct_host_list

    gpu_active = None
    gpu_utilization_max = None
    gpu_utilization_mean = None
    try:
        gpu_list = cached_orm(
            f"{KEY_GPU_QS}:{job.jid}",
            TIMEOUT_SHORT,
            lambda: list(
                host_data.objects.filter(
                    jid=job.jid,
                    type="nvidia_gpu",
                    event="utilization",
                )
                .values("type", "event", "value")
                .order_by("time")
            ),
        )
        gpu_data = DataFrame(gpu_list) if gpu_list else DataFrame()
        if not gpu_data.empty and len(gpu_data) > 2:
            gpu_data = gpu_data.iloc[1:-1]
            gpu_utilization_max = float(gpu_data["value"].max())
            gpu_utilization_mean = float(gpu_data["value"].mean())
            if not isnan(gpu_utilization_max):
                gpu_active = ceil(gpu_utilization_max / 100.0)
    except Exception:
        pass

    xalt_payload = None
    if cfg.get_xalt_user() != "":
        def _xalt_fn():
            xalt_data = xalt_data_c()
            for r in run.objects.using("xalt").filter(job_id=job.jid):
                if "usr" in r.exec_path.split("/"):
                    continue
                xalt_data.exec_path.append(r.exec_path)
                xalt_data.cwd.append(r.cwd[0:128])
                for join in join_run_object.objects.using("xalt").filter(run_id=r.run_id):
                    obj = lib.objects.using("xalt").get(obj_id=join.obj_id)
                    module_name = obj.module_name or "none"
                    if any(libtmp.module_name == module_name for libtmp in xalt_data.libset):
                        continue
                    xalt_data.libset.append(
                        libset_c(object_path=obj.object_path, module_name=module_name)
                    )
            xalt_data.exec_path = list(set(xalt_data.exec_path))
            xalt_data.cwd = list(set(xalt_data.cwd))
            xalt_data.libset = sorted(xalt_data.libset, key=lambda x: x.module_name)
            return {
                "exec_path": xalt_data.exec_path,
                "cwd": xalt_data.cwd,
                "libset": [(l.object_path, l.module_name) for l in xalt_data.libset],
            }

        xalt_payload = cached_orm(f"{KEY_XALT}:{job.jid}", TIMEOUT_SHORT, _xalt_fn)

    xalt_data = {
        "exec_path": xalt_payload["exec_path"] if xalt_payload else [],
        "cwd": xalt_payload["cwd"] if xalt_payload else [],
        "libset": xalt_payload["libset"] if xalt_payload else [],
    }

    mscript, mdiv = "", ""
    try:
        sp = plots.SummaryPlot(j)
        mscript, mdiv = components(sp.plot())
    except Exception:
        pass

    fsio = {}
    try:
        llite_df = j.get_llite_delta_by_event()
        if not llite_df.empty and "delta_sum" in llite_df.columns:
            llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
            read_row = llite_df[llite_df["event"] == "read_bytes"]
            write_row = llite_df[llite_df["event"] == "write_bytes"]
            read_val = float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0
            write_val = float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0
            fsio["llite"] = [read_val, write_val]
    except Exception:
        pass

    schema = {}
    try:
        schema = j.schema
    except Exception:
        pass

    urlstring = "https://scribe.tacc.utexas.edu/en-US/app/search/search?q=search%20"
    hoststring = urlstring + "%20host%3D" + host_list[0] + cfg.get_host_name_ext()
    serverstring = urlstring + "%20mds*%20OR%20%20oss*"
    for host in host_list[1:]:
        hoststring += "%20OR%20%20host%3D" + host + "*"
    hoststring += "&earliest=" + str(j.start_time) + "&latest=" + str(j.end_time) + "&display.prefs.events.count=50"
    serverstring += "&earliest=" + str(j.start_time) + "&latest=" + str(j.end_time) + "&display.prefs.events.count=50"

    metrics_list = [
        {"type": o.type, "metric": o.metric, "units": o.units, "value": o.value}
        for o in job.metrics_data_set.all()
    ]

    from .models import proc_data
    proc_list = list(
        proc_data.objects.filter(jid=job.jid).values_list("proc", flat=True).distinct()
    )

    return Response({
        "job_data": JobListSerializer(job).data,
        "host_list": host_list,
        "fsio": fsio,
        "xalt_data": xalt_data,
        "mscript": mscript,
        "mdiv": mdiv,
        "hscript": "",
        "hdiv": "",
        "schema": schema,
        "client_url": hoststring,
        "server_url": serverstring,
        "gpu_active": gpu_active,
        "gpu_utilization_max": gpu_utilization_max,
        "gpu_utilization_mean": gpu_utilization_mean,
        "metrics_list": metrics_list,
        "proc_list": proc_list,
    })


@api_view(["GET"])
def type_detail(request, jid, type_name):
    """Type detail: Bokeh tscript/tdiv, stats_data, schema."""
    err = _require_auth(request)
    if err is not None:
        return err

    job = cached_orm(
        f"{KEY_JOB}:{jid}",
        TIMEOUT_SHORT,
        lambda: job_data.objects.filter(jid=jid).first(),
    )
    if not job:
        return Response({"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND)

    acct_host_list = [h + "." + cfg.get_host_name_ext() for h in (job.host_list or [])]
    start_time = job.start_time
    end_time = job.end_time
    if start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time, timezone.utc)
    if end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time, timezone.utc)
    start_time = start_time.astimezone(local_timezone)
    end_time = end_time.astimezone(local_timezone)

    from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider
    provider = TypeDetailDataProvider(jid, type_name, start_time, end_time, acct_host_list)

    def _type_hosts_fn():
        return list(
            host_data.objects.filter(
                jid=jid,
                type=type_name,
                time__gte=start_time,
                time__lte=end_time,
                host__in=acct_host_list,
            )
            .values_list("host", flat=True)
            .distinct()
        )

    _st = start_time.isoformat() if start_time else ""
    _et = end_time.isoformat() if end_time else ""
    data_host_list = cached_orm(
        f"{KEY_TYPE_DETAIL_HOSTS}:{jid}:{type_name}:{_st}:{_et}",
        TIMEOUT_SHORT,
        _type_hosts_fn,
    )
    if len(data_host_list) == 0:
        return Response({
            "type_name": type_name,
            "jobid": jid,
            "tscript": "",
            "tdiv": "",
            "stats_data": [],
            "schema": [],
        })

    sp = plots.DevPlot(provider, data_host_list)
    df, plot = sp.plot()
    tscript, tdiv = components(plot)
    schema = [
        c for c in df.columns
        if c not in ("host", "time", "index")
    ] if not df.empty else []

    stats_data = []
    if not df.empty and "time" in df.columns and len(df) > 0 and schema:
        df = df.copy()
        df["dt"] = df["time"].sub(df["time"].iloc[0]).astype("timedelta64[s]")
        df1 = df.groupby("dt")[schema].mean().reset_index()
        for t in range(len(df1)):
            vals = df1.loc[df1.index[t], schema].values.flatten().tolist()
            vals = [float(x) if hasattr(x, "__float__") else x for x in vals]
            stats_data.append([str(df1["dt"].iloc[t]), vals])

    return Response({
        "type_name": type_name,
        "jobid": jid,
        "tscript": tscript,
        "tdiv": tdiv,
        "stats_data": stats_data,
        "schema": schema,
    })


@api_view(["GET"])
def admin_monitor(request):
    """Staff-only: host last-seen timestamps and age buckets."""
    err = _require_auth(request)
    if err is not None:
        return err
    if not request.session.get("is_staff", False):
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    def _all_hosts_fn():
        qs = job_data.objects.distinct("host_list").values_list("host_list", flat=True)
        flat = [host for sublist in qs for host in sublist]
        return list(set(flat))

    all_hosts = cached_orm(KEY_ALL_HOSTS, TIMEOUT_MEDIUM, _all_hosts_fn)
    now = timezone.now()
    time_bounds = now - timedelta(days=8)
    _tb = time_bounds.isoformat()

    host_stats = []
    for host in all_hosts:
        def _host_last_fn(h=host, tb=_tb):
            row = host_data.objects.filter(
                host__icontains=h, time__gte=time_bounds
            ).order_by("-time").first()
            return row.time if row else None

        last_time = cached_orm(
            f"{KEY_HOST_LAST}:{host}:{_tb}",
            TIMEOUT_SHORT,
            _host_last_fn,
        )
        if last_time is None:
            host_stats.append({"host": host, "last_time": None, "age_bucket": "gt_week"})
            continue
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
            "last_time": last_time.isoformat() if last_time else None,
            "age_bucket": bucket,
        })

    return Response({"host_stats": host_stats})
