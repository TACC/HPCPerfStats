"""Massive ``host_data`` stress: seed + real ``update_metrics(..., rerun=True)`` + JSON report.

**Not run by default.** This directory is outside ``pyproject.toml`` ``testpaths``
(``hpcperfstats`` only). CI and ``python scripts/run_tests.py`` never collect these tests.

**Default: Docker Compose workflow** (PostgreSQL + Redis, migrate, env in-container):

.. code-block:: bash

   cd HPCPerfStats
   tests/run_stress_host_data_workflow.sh

**Row sizing**

- **Legacy (default in workflow):** ``HPCPERFSTATS_STRESS_HOST_DATA_ROWS`` (default
  **400000**), optional ``HPCPERFSTATS_STRESS_N_HOSTS`` and
  ``HPCPERFSTATS_STRESS_INTERVAL_SEC`` (default **1** for this mode).
- **Time-rectangle:** ``HPCPERFSTATS_STRESS_USE_TIME_SCALE=1`` with
  ``HPCPERFSTATS_STRESS_N_HOSTS``, ``HPCPERFSTATS_STRESS_INTERVAL_SEC`` (e.g. **30**),
  ``HPCPERFSTATS_STRESS_DURATION_SEC``.

**Readiness:** seed inserts one post-``end_time`` row per host so
``update_metrics`` readiness filtering passes.

**Date passed to ``update_metrics``:** uses ``timezone.localtime(job.end_time)`` so
``end_time__date`` in ``_jobs_queryset`` matches Django’s configured timezone (UTC
``job.end_time`` alone can cross a local calendar day for long windows and yield
an empty queryset).

**Report:** ``stress_report_<utc>.json`` under ``HPCPERFSTATS_STRESS_REPORT_DIR``
(default ``artifacts/stress/``). Includes **R_ref** from
``monitor_sample_density.analyze_monitor_sample_density`` and extrapolated
``6000 × 8640 × R_max`` row estimate.

**Optional**

- ``HPCPERFSTATS_STRESS_PLOT_SEC`` — when ``HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY=1``,
  fail if any single manual plot exceeds this many seconds.
- ``HPCPERFSTATS_STRESS_EXPLAIN=1`` — attach one ``EXPLAIN (FORMAT JSON)`` snapshot
  (representative host-in + time range query).
- ``HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`` / ``HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`` —
  jid_table sampling (see ``conf_parser``).

Profiling lines are printed as ``stress_timing: ...``; the JSON report lists phases.
When ``jid_table_init`` runs, additional phases ``jid_init_row_count_sql`` and
``jid_init_strided_times_sql`` break out large-job gate SQL (skipped when below threshold).
"""
from __future__ import annotations

import os
import time

import pytest
from django.db import connection
from django.utils import timezone as django_timezone

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.gen import jid_table as jid_table_mod
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)
from hpcperfstats.analysis.metrics.metrics import expected_job_metric_row_count
from hpcperfstats.analysis.metrics.update_metrics import (
    _latest_sample_time_by_host,
    update_metrics,
)
from hpcperfstats.site.machine.cache_utils import invalidate_job_plot_cache_keys_for_jids
from hpcperfstats.site.machine.job_plot_artifacts import (
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_NORMAL,
    JOB_PLOT_LAYOUT_ZOOM_V3,
    compute_plot_item_for_kind,
)
from hpcperfstats.site.machine.models import host_data, job_data, job_plot_artifact

from .monitor_sample_density import analyze_monitor_sample_density
from .stress_profiler import StressProfiler
from .stress_seed_massive import (
    insert_stress_job_and_host_data,
    stress_host_fqdns,
    stress_jid,
    stress_seed_dimensions,
    target_row_count,
)


def _log(msg: str) -> None:
  print("stress_timing: {}".format(msg), flush=True)


def _gather_counts(jid: str) -> dict:
  exp = expected_job_metric_row_count()
  md_count = job_data.objects.get(jid=jid).metrics_data_set.count()
  art_count = job_plot_artifact.objects.filter(jid_id=jid).count()
  hd_count = host_data.objects.filter(jid=jid).count()
  return {
      "expected_metrics_catalog_rows": exp,
      "metrics_data_rows": md_count,
      "job_plot_artifact_rows": art_count,
      "host_data_rows_jid": hd_count,
  }


@pytest.mark.stress_host_data
@pytest.mark.django_db(transaction=True)
def test_massive_host_data_stress_full_pipeline():
  if connection.vendor != "postgresql":
    pytest.skip("Stress suite requires PostgreSQL (Timescale).")

  plot_budget = float(os.environ.get("HPCPERFSTATS_STRESS_PLOT_SEC", "0") or "0")
  manual_plots = os.environ.get(
      "HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY", ""
  ).strip().lower() in ("1", "true", "yes")

  profiler = StressProfiler()
  density = analyze_monitor_sample_density()
  dims = stress_seed_dimensions()
  profiler.set_metadata(
      r_ref_from_sample=density,
      stress_seed_dimensions={
          "n_hosts": dims.n_hosts,
          "n_steps": dims.n_steps,
          "interval_sec": dims.interval_sec,
          "n_metrics": dims.n_metrics,
          "n_rows": dims.n_rows,
          "use_time_scale": dims.use_time_scale,
      },
      extrapolated_full_scale_rows_6000x8640={
          "using_r_max": (6000 * 8640 * (density.get("r_max") or 0)),
          "using_r_median": (6000 * 8640 * (density.get("r_median") or 0)),
      },
  )

  report_path = None
  old_timeout = None
  with connection.cursor() as cursor:
    cursor.execute("SHOW statement_timeout")
    row = cursor.fetchone()
    if row:
      old_timeout = row[0]
    cursor.execute("SET SESSION statement_timeout = '3600s'")

  try:

    def _seed():
      return insert_stress_job_and_host_data(host_ext=cfg.get_host_name_ext())

    t0 = time.perf_counter()
    jid, _fqdn, n_rows, live_expected = profiler.phase("seed_bulk_insert_probes", _seed)
    profiler.record_phase(
        "seed_wall_including_profiler_overhead",
        time.perf_counter() - t0,
    )

    _log("seed_done jid={} rows={} live_expected={}".format(
        jid, n_rows, live_expected))

    assert jid == stress_jid()
    assert n_rows == stress_seed_dimensions().n_rows
    if not dims.use_time_scale:
      assert n_rows == target_row_count()

    def _orm_count():
      return host_data.objects.filter(jid=jid).count()

    orm_count = profiler.phase("orm_verification_host_data_count", _orm_count)
    probe_rows = dims.n_hosts
    assert orm_count == n_rows + probe_rows

    suffix = "." + cfg.get_host_name_ext()

    def _live():
      live_row = (
          job_data.objects.filter(jid=jid)
          .annotate(live=live_distinct_host_time_count_expression(suffix))
          .values("live")
          .first()
      )
      return int(live_row["live"]) if live_row and live_row.get("live") is not None else 0

    live = profiler.phase("live_distinct_host_time_count", _live)
    _log("live_distinct live={} expected={}".format(live, live_expected))
    assert live == live_expected

    job = job_data.objects.get(jid=jid)
    acct_hosts = stress_host_fqdns(cfg.get_host_name_ext(), dims.n_hosts)

    def _latest():
      return _latest_sample_time_by_host(acct_hosts)

    latest = profiler.phase("latest_sample_time_by_host", _latest)
    for h in acct_hosts:
      assert h in latest and latest[h] is not None
      assert latest[h] > job.end_time

    profiler.maybe_explain_chunked_host_in(
        jid,
        acct_hosts,
        job.start_time,
        job.end_time,
    )

    def _pre_pg():
      profiler.snapshot_pg("pre_update_metrics")

    profiler.phase("pg_introspection_pre_update_metrics", _pre_pg)

    invalidate_job_plot_cache_keys_for_jids([jid])

    _orig_row_count = jid_table_mod._count_host_data_rows_for_window
    _orig_strided = jid_table_mod._strided_distinct_times_for_large_job

    def _wrap_row_count(*args, **kwargs):
      t0 = time.perf_counter()
      try:
        return _orig_row_count(*args, **kwargs)
      finally:
        profiler.record_phase(
            "jid_init_row_count_sql",
            time.perf_counter() - t0,
        )

    def _wrap_strided(*args, **kwargs):
      t0 = time.perf_counter()
      try:
        return _orig_strided(*args, **kwargs)
      finally:
        profiler.record_phase(
            "jid_init_strided_times_sql",
            time.perf_counter() - t0,
        )

    def _jt():
      jid_table_mod._count_host_data_rows_for_window = _wrap_row_count
      jid_table_mod._strided_distinct_times_for_large_job = _wrap_strided
      try:
        return jid_table_mod.jid_table(jid)
      finally:
        jid_table_mod._count_host_data_rows_for_window = _orig_row_count
        jid_table_mod._strided_distinct_times_for_large_job = _orig_strided

    jt = profiler.phase("jid_table_init", _jt)
    _log("jid_table token={} hosts={}".format(
        getattr(jt, "_large_job_plot_cache_token", "?"),
        len(jt.host_list or []),
    ))
    assert jt.host_list

    def _um():
      # _jobs_queryset filters end_time__date using the project timezone; localtime
      # matches that extraction (raw UTC job.end_time can differ by calendar day).
      update_metrics(django_timezone.localtime(job.end_time), rerun=True)

    profiler.phase("update_metrics_rerun_true", _um)

    counts = _gather_counts(jid)
    profiler.set_counts(**counts)
    _log(
        "post_update_metrics metrics_rows={}/{} plot_artifacts={}".format(
            counts["metrics_data_rows"],
            counts["expected_metrics_catalog_rows"],
            counts["job_plot_artifact_rows"],
        )
    )

    assert counts["metrics_data_rows"] == counts["expected_metrics_catalog_rows"]
    assert counts["job_plot_artifact_rows"] >= 1

    if manual_plots:
      jt2 = jid_table_mod.jid_table(jid)
      for layout_name, zoom in (
          (JOB_PLOT_LAYOUT_NORMAL, False),
          (JOB_PLOT_LAYOUT_ZOOM_V3, True),
      ):
        for kind in JOB_PLOT_KINDS:
          t_pl = time.perf_counter()
          item, reason = compute_plot_item_for_kind(jt2, kind, zoom)
          elapsed = time.perf_counter() - t_pl
          profiler.record_phase(
              "manual_plot",
              elapsed,
              kind=kind,
              layout=layout_name,
              ok=item is not None,
              reason=str(reason)[:200] if reason else "",
          )
          _log("manual_plot kind={} layout={} s={:.3f} ok={} reason={!r}".format(
              kind, layout_name, elapsed, item is not None, reason))
          if plot_budget > 0:
            assert elapsed < plot_budget, (
                "plot {} {} exceeded HPCPERFSTATS_STRESS_PLOT_SEC={}".format(
                    kind, layout_name, plot_budget))

    def _post_pg():
      profiler.snapshot_pg("post_update_metrics")

    profiler.phase("pg_introspection_post_update_metrics", _post_pg)

  finally:
    report_path = profiler.write_report()
    _log("stress_report_written {}".format(report_path))
    with connection.cursor() as cursor:
      if old_timeout is not None:
        cursor.execute("SET SESSION statement_timeout = %s", [old_timeout])
      else:
        cursor.execute("SET SESSION statement_timeout = DEFAULT")
