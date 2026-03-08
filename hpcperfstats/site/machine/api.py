"""Django REST Framework API views for machine app. All data via JSON for React SPA."""
import logging
from datetime import timezone as dt_timezone

import hpcperfstats.conf_parser as cfg
from bokeh.embed import components, json_item
from bokeh.layouts import gridplot
from bokeh.plotting import figure
from django.utils import timezone
from django.views.decorators.cache import cache_page
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
from .query_utils import (
    expand_month_date_to_range,
    get_job_list_order_by,
    normalize_job_list_query_params,
)
from .serializers import JobListSerializer
from .views import (
    job_hist,
    local_timezone,
    libset_c,
    xalt_data_c,
)
from django.db.models import Exists, OuterRef
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from datetime import timedelta
from numpy import isnan
from math import ceil

import hpcperfstats.analysis.gen.jid_table as jid_table
from hpcperfstats.analysis.gen.jid_table import HostDataProvider
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


@cache_page(TIMEOUT_MEDIUM)
@api_view(["GET"])
def home_options(request):
    """Return options for search form: date_list, metrics, queues, states, machine_name."""
    err = _require_auth(request)
    if err is not None:
        return err

    def _dates_fn():
        return sorted(job_data.objects.dates("end_time", "day"))

    date_list = cached_orm(KEY_DATES, TIMEOUT_MEDIUM, _dates_fn)
    month_dict = {}
    if date_list:
        for d in date_list:
            key = f"{d.year}-{d.month:02d}"  # YYYY-MM
            if key not in month_dict:
                month_dict[key] = []
            month_dict[key].append((str(d), str(d.day)))

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
        job_jid = cached_orm(
            f"{KEY_JOB}:{jid}",
            TIMEOUT_SHORT,
            lambda: job_data.objects.filter(jid=jid).values_list("jid", flat=True).first(),
        )
        if job_jid:
            return Response({"redirect": f"/machine/job/{job_jid}/"})
        return Response(
            {"error": "No result found in search"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.GET.get("host"):
        # Redirect to SPA host plot page with query params
        host = request.GET.get("host", "").strip()
        if host:
            q = request.GET.copy()
            q.pop("host", None)
            query = q.urlencode()
            path = f"/machine/host/{host}/plot/"
            if query:
                path = f"{path}?{query}"
            return Response({"redirect": path})
        return job_list(request)

    return job_list(request)


def _job_list_histograms_empty_figure():
    """Return a single Bokeh figure for empty histogram state (no jobs or no plottable metrics)."""
    empty = figure(
        height=400,
        width=600,
        title="No histogram data available for this job list.",
        toolbar_location=None,
    )
    return gridplot([[empty]], toolbar_location=None)


def _job_list_histograms(request):
    """Build Bokeh script/div and json_item for job list histograms. Returns (script, div, plot_item)."""
    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics" and k not in ("page", "order_by")
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    job_list_qs = job_data.objects.filter(**acct_data)
    if order_by.lstrip("-") == "has_metrics":
        job_list_qs = job_list_qs.annotate(
            has_metrics=Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
        )
    job_list_qs = job_list_qs.order_by(order_by)

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
        gp = _job_list_histograms_empty_figure()
        script, div = components(gp)
        return script, div, json_item(gp)

    acc_cols = ["jid", "start_time", "submit_time", "runtime", "nhosts"]
    job_rows = list(job_list_qs.values(*acc_cols))
    jids_ordered = [r["jid"] for r in job_rows]
    job_df = DataFrame(job_rows).set_index("jid")

    metrics_rows = list(
        metrics_data.objects.filter(jid_id__in=jids_ordered).values(
            "jid_id", "metric", "units", "value"
        )
    )
    metric_dict = {}
    hist_metrics_set = set()
    for row in metrics_rows:
        jid_id = row["jid_id"]
        metric_dict.setdefault(row["metric"], []).append((jid_id, row["value"]))
        hist_metrics_set.add((row["metric"], row["units"]))

    jid_dict = {"jid": jids_ordered}
    for name in df_fields:
        jid_to_val = {jid: val for jid, val in metric_dict.get(name, [])}
        jid_dict[name] = [jid_to_val.get(j, None) for jid in jids_ordered]
    df = DataFrame(jid_dict).set_index("jid")
    hist_metrics = list(hist_metrics_set)
    df = df.join(job_df)
    hist_metrics += [("runtime", "hours"), ("nhosts", "#nodes"), ("queue_wait", "hours")]
    df["queue_wait"] = (
        to_timedelta(df["start_time"] - df["submit_time"]).dt.total_seconds() / 3600
    )
    df["runtime"] = df["runtime"] / 3600
    df = clean_dataframe(df)

    # Only plot metrics that exist as columns (df has filter metrics + runtime/nhosts/queue_wait)
    hist_metrics = [(m, label) for m, label in hist_metrics if m in df.columns]

    script = ""
    div = ""
    plot_item = None
    try:
        plot_list = [job_hist(df, metric, label) for metric, label in hist_metrics]
        plot_list = [p for p in plot_list if p is not None]
        if plot_list:
            gp = gridplot(plot_list, ncols=2)
            script, div = components(gp)
            plot_item = json_item(gp)
        else:
            gp = _job_list_histograms_empty_figure()
            script, div = components(gp)
            plot_item = json_item(gp)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to generate job list histograms: %s", e, exc_info=True
        )
        gp = _job_list_histograms_empty_figure()
        script, div = components(gp)
        plot_item = json_item(gp)
    return script, div, plot_item


@api_view(["GET"])
def job_list_histograms(request):
    """Return Bokeh script/div and plot_item for job list histograms (same query params as job list)."""
    err = _require_auth(request)
    if err is not None:
        return err
    script, div, plot_item = _job_list_histograms(request)
    return Response({"script": script, "div": div, "plot_item": plot_item})


@api_view(["GET"])
def job_list(request):
    """Paginated job list only (histograms via separate job_list_histograms endpoint)."""
    err = _require_auth(request)
    if err is not None:
        return err

    fields = request.GET.dict()
    fields = {k: v for k, v in fields.items() if v}
    fields = normalize_job_list_query_params(fields)
    fields = expand_month_date_to_range(fields)

    acct_data = {
        k: v
        for k, v in fields.items()
        if k.split("_", 1)[0] != "metrics" and k not in ("page", "order_by")
    }
    order_by = get_job_list_order_by(fields) or "-end_time"
    job_list_qs = job_data.objects.filter(**acct_data)
    if order_by.lstrip("-") == "has_metrics":
        job_list_qs = job_list_qs.annotate(
            has_metrics=Exists(metrics_data.objects.filter(jid_id=OuterRef("jid")))
        )
    job_list_qs = job_list_qs.order_by(order_by)

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

    job_list_qs = job_list_qs.prefetch_related("metrics_data_set")
    nj = job_list_qs.count()

    if nj == 0:
        return Response(
            {"error": "No data found for this search request"},
            status=status.HTTP_404_NOT_FOUND,
        )

    page_num = request.GET.get("page", 1)
    paginator = Paginator(job_list_qs, min(100, nj))
    try:
        page = paginator.page(page_num)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    current_path = request.get_full_path() if "?" in request.get_full_path() else None
    qname = "Jobs"
    if fields.get("queue"):
        qname = f"Jobs in queue {fields['queue']}"

    return Response({
        "job_list": JobListSerializer(page.object_list, many=True).data,
        "nj": nj,
        "current_path": current_path,
        "qname": qname,
        "order_by": order_by,
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
        lambda: job_data.objects.filter(jid=pk)
        .prefetch_related("metrics_data_set")
        .first(),
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
            runs = list(
                run.objects.using("xalt")
                .filter(job_id=job.jid)
                .only("exec_path", "cwd", "run_id")
            )
            run_ids = [r.run_id for r in runs]
            joins = list(
                join_run_object.objects.using("xalt")
                .filter(run_id__in=run_ids)
                .only("run_id", "obj_id")
            ) if run_ids else []
            obj_ids = list(set(j.obj_id for j in joins))
            libs_by_id = {
                l.obj_id: l
                for l in lib.objects.using("xalt")
                .filter(obj_id__in=obj_ids)
                .only("object_path", "module_name")
            } if obj_ids else {}
            joins_by_run = {}
            for j in joins:
                joins_by_run.setdefault(j.run_id, []).append(j)
            for r in runs:
                if "usr" in (r.exec_path or "").split("/"):
                    continue
                xalt_data.exec_path.append(r.exec_path)
                xalt_data.cwd.append((r.cwd or "")[0:128])
                for join in joins_by_run.get(r.run_id, []):
                    obj = libs_by_id.get(join.obj_id)
                    if not obj:
                        continue
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
    mplot_item = None
    mplot_unavailable_reason = None
    try:
        sp = plots.SummaryPlot(j)
        plot = sp.plot()
        mscript, mdiv = components(plot)
        mplot_item = json_item(plot)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to generate summary plot for jid %s: %s", job.jid, e, exc_info=True
        )
        mplot_unavailable_reason = str(e)

    hscript, hdiv = "", ""
    hplot_item = None
    hplot_unavailable_reason = None
    try:
        hm_fig = plots.plot_from_jid_table(j)
        if hm_fig is not None:
            hscript, hdiv = components(hm_fig)
            hplot_item = json_item(hm_fig)
        else:
            hplot_unavailable_reason = plots.MSG_NO_HOST_MSR_DATA
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to generate heatmap for jid %s: %s", job.jid, e, exc_info=True
        )
        hplot_unavailable_reason = str(e)

    rscript, rdiv = "", ""
    rplot_item = None
    rplot_unavailable_reason = None
    try:
        roof_fig = plots.plot_roofline_from_jid_table(j)
        if roof_fig is not None:
            rscript, rdiv = components(roof_fig)
            rplot_item = json_item(roof_fig)
        else:
            rplot_unavailable_reason = plots.MSG_NO_ROOFLINE_DATA
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to generate roofline for jid %s: %s", job.jid, e, exc_info=True
        )
        rplot_unavailable_reason = str(e)

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
        "mplot_item": mplot_item,
        "mplot_unavailable_reason": mplot_unavailable_reason,
        "hscript": hscript,
        "hdiv": hdiv,
        "hplot_item": hplot_item,
        "hplot_unavailable_reason": hplot_unavailable_reason,
        "rscript": rscript,
        "rdiv": rdiv,
        "rplot_item": rplot_item,
        "rplot_unavailable_reason": rplot_unavailable_reason,
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
        lambda: job_data.objects.filter(jid=jid)
        .only("host_list", "start_time", "end_time")
        .first(),
    )
    if not job:
        return Response({"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND)

    acct_host_list = [h + "." + cfg.get_host_name_ext() for h in (job.host_list or [])]
    start_time = job.start_time
    end_time = job.end_time
    if start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time, dt_timezone.utc)
    if end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time, dt_timezone.utc)
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
            "tplot_item": None,
            "stats_data": [],
            "schema": [],
        })

    sp = plots.DevPlot(provider, data_host_list)
    df, plot = sp.plot()
    tscript, tdiv = components(plot)
    tplot_item = json_item(plot)
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
        "tplot_item": tplot_item,
        "stats_data": stats_data,
        "schema": schema,
    })


@api_view(["GET"])
def host_plot(request):
    """Return Bokeh plot_item for a single host and time range (GET host, end_time__gte, end_time__lte)."""
    err = _require_auth(request)
    if err is not None:
        return err

    host_fqdn = (request.GET.get("host") or "").strip()
    start_time = request.GET.get("end_time__gte", "").strip()
    end_time = (request.GET.get("end_time__lte") or "now()").strip()

    if not host_fqdn or not start_time:
        return Response(
            {"error": "Missing host or end_time__gte"},
            status=status.HTTP_400_BAD_REQUEST,
        )

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
        start_dt = timezone.make_aware(start_dt, dt_timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = timezone.make_aware(end_dt, dt_timezone.utc)
    start_dt = start_dt.astimezone(local_timezone)
    end_dt = end_dt.astimezone(local_timezone)

    plot_item = None
    try:
        ht = HostDataProvider(host_fqdn, start_dt, end_dt)
        sp = plots.SummaryPlot(ht)
        plot = sp.plot()
        plot_item = json_item(plot)
    except Exception:
        pass

    return Response({
        "host": host_fqdn,
        "plot_item": plot_item,
        "end_time__gte": start_dt.isoformat(),
        "end_time__lte": end_dt.isoformat(),
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
