#!/usr/bin/env python3
"""
Build, capture and verify the update_metrics golden corpus (kept outside git).

The corpus is the parity oracle for the columnar (pandas-free, Codon) metrics
path: real jobs ingested from raw monitor stats files plus synthetic edge
jobs, with the pandas path's outputs (``metrics_data`` rows, Job Detail
payloads, plot series and plot JSON) snapshotted per job.

Subcommands (run ``seed-*`` and ``capture`` inside the dev container through
``tests/run_dev_container_pytest.sh --exec``; ``verify`` runs anywhere):

* ``seed-exemplars``: ingest raw stats files (``add_stats_file_to_db``) listed
  in a manifest or discovered under ``--raw-root``.
* ``seed-job-data``: create ``job_data`` rows for every job id found in
  ``host_data`` for the seeded hosts (start/end from first/last sample).
* ``verify``: check the corpus manifest and fixture files.

Attributes:
  DEFAULT_CORPUS_DIR: Default out-of-git corpus directory on the dev host.
  CORES_PER_HOST_BY_RACK_PREFIX: Allocated cores per node assumed per
    exemplar rack prefix (first two characters of the short host name) when
    synthesizing ``job_data.ncores``; the clusters' FQDN suffixes are
    rewritten to the dev INI suffix by ``rewrite-host-suffix``.
  DEFAULT_CORES_PER_HOST: Fallback cores per node for unknown suffixes.
  MIN_SAMPLES_PER_JOB: Jobs with fewer distinct sample times are skipped
    when synthesizing ``job_data`` (no time axis for rates).
  REQUIRED_EDGE_CASES: Edge-case tags the manifest must carry for ``verify``.
  EDGE_CASE_TAGS: Synthetic edge job id to its tags (``tag-edge-jobs``).
  EDGE_BASE_EPOCH: First sample epoch of every synthetic edge job.
  EDGE_SAMPLE_INTERVAL_S: Seconds between synthetic samples.
  DCGM_FP64_BLANK_TEXT: DCGM FP64 blank sentinel as monitor text.
  SEEDED_FILES_LOG: Manifest-relative log of ingested raw paths (idempotence).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

DEFAULT_CORPUS_DIR = "/data/user/sharrell/hpcperfstats-metrics-golden"
CORES_PER_HOST_BY_RACK_PREFIX: dict[str, int] = {
  "c1": 144,  # horizon (Grace superchip)
  "c3": 128,  # ls6 (Milan)
  "v3": 128,  # ls6 GPU nodes
  "c4": 112,  # stampede3 (SPR)
  "c5": 112,  # stampede3 (SPR)
}
DEFAULT_CORES_PER_HOST = 64
MIN_SAMPLES_PER_JOB = 3
REQUIRED_EDGE_CASES = (
  "counter_rollover",
  "missing_host",
  "single_sample",
  "gpu_poison",
  "small_job",
  "shared_node",
  "no_data",
)
SEEDED_FILES_LOG = "seeded_raw_files.txt"


def _corpus_dir(arg: str | None) -> Path:
  """
  Resolve the corpus directory from the CLI argument or the environment.

  Args:
    arg (str | None): ``--dir`` value, or None to use
      ``HPCPERFSTATS_METRICS_GOLDEN_DIR`` then ``DEFAULT_CORPUS_DIR``.

  Returns:
    Path: Corpus directory (not created).

  Examples:
    >>> str(_corpus_dir("/tmp/x"))
    '/tmp/x'
  """
  if arg:
    return Path(arg)
  env = os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_DIR", "").strip()
  return Path(env or DEFAULT_CORPUS_DIR)


def _ensure_django() -> None:
  """
  Bootstrap Django settings so ORM imports work inside the container.

  Returns:
    None

  Examples:
    >>> _ensure_django()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()


def iter_raw_stats_files(raw_root: Path, max_bytes: int) -> Iterable[Path]:
  """
  Yield ``<root>/<host>/<epoch>`` raw stats files no larger than ``max_bytes``.

  Both the flat exemplar layout (``<root>/<host-fqdn>/<epoch>``) and a nested
  ``<root>/archive/<host-fqdn>/<epoch>`` layout are accepted.

  Args:
    raw_root (Path): Directory holding per-host raw stats directories.
    max_bytes (int): Skip files larger than this many bytes.

  Yields:
    Path: Candidate raw stats files, sorted by size ascending.

  Examples:
    >>> list(iter_raw_stats_files(Path("/nonexistent"), 10))
    []
  """
  if not raw_root.is_dir():
    return
  roots = [raw_root]
  nested = raw_root / "archive"
  if nested.is_dir():
    roots.append(nested)
  found: list[tuple[int, Path]] = []
  for root in roots:
    for host_dir in sorted(root.iterdir()):
      if not host_dir.is_dir() or "." not in host_dir.name:
        continue
      for path in sorted(host_dir.iterdir()):
        if not path.is_file() or not path.name.isdigit():
          continue
        size = path.stat().st_size
        if size <= max_bytes:
          found.append((size, path))
  for _size, path in sorted(found):
    yield path


def raw_file_job_ids(path: Path, min_samples: int) -> dict[str, int]:
  """
  Count sample headers per job id in a raw stats file.

  Sample header lines are ``<epoch> <jid> <hostname>``; ``-`` marks idle.

  Args:
    path (Path): Raw stats file.
    min_samples (int): Keep only job ids with at least this many headers.

  Returns:
    dict[str, int]: Job id -> sample header count (idle ``-`` excluded).

  Examples:
    >>> raw_file_job_ids(Path("/nonexistent"), 1)
    {}
  """
  counts: dict[str, int] = {}
  try:
    with open(path, encoding="ascii", errors="replace") as fh:
      for line in fh:
        if not line or not line[0].isdigit():
          continue
        parts = line.split()
        if len(parts) < 3:
          continue
        jid = parts[1]
        if jid == "-":
          continue
        counts[jid] = counts.get(jid, 0) + 1
  except OSError:
    return {}
  return {j: n for j, n in counts.items() if n >= min_samples}


def _load_seeded(corpus_dir: Path) -> set[str]:
  """
  Read the set of raw paths already ingested into the dev database.

  Args:
    corpus_dir (Path): Corpus directory holding ``SEEDED_FILES_LOG``.

  Returns:
    set[str]: Absolute raw paths recorded as ingested.

  Examples:
    >>> _load_seeded(Path("/nonexistent"))
    set()
  """
  log = corpus_dir / SEEDED_FILES_LOG
  if not log.is_file():
    return set()
  return {ln.strip() for ln in log.read_text().splitlines() if ln.strip()}


def cmd_seed_exemplars(args: argparse.Namespace) -> int:
  """
  Ingest raw exemplar stats files into ``host_data`` via ``add_stats_file_to_db``.

  Runs the production ingest path (Codon parse when the pyext is present) one
  file at a time, archive side effects disabled, recording each ingested path
  in ``SEEDED_FILES_LOG`` so reruns skip finished files.

  Args:
    args (argparse.Namespace): ``raw_root``, ``max_bytes``, ``min_samples``,
      ``limit``, ``dir``.

  Returns:
    int: Process exit code (0 when every attempted file ingested).

  Examples:
    >>> cmd_seed_exemplars(
    ...   argparse.Namespace(  # doctest: +SKIP
    ...     raw_root="/corpus/raw",
    ...     max_bytes=500_000_000,
    ...     min_samples=3,
    ...     limit=0,
    ...     dir=None,
    ...   )
    ... )
  """
  corpus_dir = _corpus_dir(args.dir)
  corpus_dir.mkdir(parents=True, exist_ok=True)
  seeded = _load_seeded(corpus_dir)
  candidates: list[Path] = []
  for path in iter_raw_stats_files(Path(args.raw_root), int(args.max_bytes)):
    if str(path) in seeded:
      continue
    if not raw_file_job_ids(path, int(args.min_samples)):
      continue
    candidates.append(path)
    if args.limit and len(candidates) >= int(args.limit):
      break
  print(f"seed-exemplars: candidates={len(candidates)} already={len(seeded)}")
  if not candidates:
    return 0
  _ensure_django()
  from hpcperfstats.dbload import sync_timedb as st

  st.should_archive = False
  failed = 0
  log = corpus_dir / SEEDED_FILES_LOG
  for index, path in enumerate(candidates):
    t0 = time.monotonic()
    result = st.add_stats_file_to_db(str(path))
    ok = (
      bool(result[2])
      if isinstance(result, tuple) and len(result) > 2
      else False
    )
    elapsed = time.monotonic() - t0
    print(
      f"seed-exemplars: [{index + 1}/{len(candidates)}] path={path} "
      f"size={path.stat().st_size} ok={int(ok)} elapsed_s={elapsed:.1f}",
      flush=True,
    )
    if ok:
      with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"{path}\n")
    else:
      failed += 1
  return 1 if failed else 0


def cmd_rewrite_host_suffix(args: argparse.Namespace) -> int:
  """
  Rewrite every ``host_data.host`` to ``<short>.<INI host_name_ext>``.

  ``jid_table`` appends the site ``host_name_ext`` to accounting host names,
  so exemplar rows from other clusters are only visible to metrics when their
  FQDN suffix matches the dev INI. Short host names stay unique across the
  three exemplar clusters. Also deletes ``job_data`` rows of
  ``--username`` (cascading metrics and artifacts) so ``seed-job-data`` can
  rebuild them with the new names. Dev-database only.

  Args:
    args (argparse.Namespace): ``username``, ``dir``.

  Returns:
    int: Process exit code (0).

  Examples:
    >>> cmd_rewrite_host_suffix(  # doctest: +SKIP
    ...   argparse.Namespace(username="corpus", dir=None)
    ... )
  """
  _ensure_django()
  from django.db import connection

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.site.lib.machine.models import job_data

  ext = str(cfg.get_host_name_ext() or "").strip().lstrip(".")
  if not ext:
    print("rewrite-host-suffix: DEFAULT.host_name_ext is empty")
    return 1
  deleted, _ = job_data.objects.filter(username=str(args.username)).delete()
  with connection.cursor() as cursor:
    cursor.execute(
      "UPDATE host_data SET host = split_part(host, '.', 1) || %s "
      "WHERE host IS NOT NULL AND host NOT LIKE %s",
      ["." + ext, "%." + ext],
    )
    rewritten = cursor.rowcount
  print(
    f"rewrite-host-suffix: suffix=.{ext} rows_rewritten={rewritten} "
    f"job_rows_deleted={deleted}"
  )
  return 0


def _cores_per_host(host: str) -> int:
  """
  Allocated cores per node for a host, by exemplar rack prefix.

  Args:
    host (str): Host FQDN or short name.

  Returns:
    int: Cores per node (``DEFAULT_CORES_PER_HOST`` for unknown prefixes).

  Examples:
    >>> _cores_per_host("c301-010.cluster_name.domain.edu")
    128
  """
  short = host.split(".", 1)[0]
  return CORES_PER_HOST_BY_RACK_PREFIX.get(short[:2], DEFAULT_CORES_PER_HOST)


def cmd_seed_job_data(args: argparse.Namespace) -> int:
  """
  Create ``job_data`` rows for job ids present in ``host_data``.

  One job per distinct ``host_data.jid``; ``host_list`` is every host with
  rows for that jid, the window is the first and last sample, and
  ``ncores`` assumes whole nodes (``CORES_PER_HOST_BY_RACK_PREFIX``). Existing
  rows are left alone. Jobs with fewer than ``--min-samples`` distinct
  sample times are skipped.

  Args:
    args (argparse.Namespace): ``min_samples``, ``username``, ``dir``.

  Returns:
    int: Process exit code (0).

  Examples:
    >>> cmd_seed_job_data(
    ...   argparse.Namespace(  # doctest: +SKIP
    ...     min_samples=3, username="corpus", dir=None
    ...   )
    ... )
  """
  _ensure_django()
  from django.db import connection

  from hpcperfstats.site.lib.machine.models import job_data

  sql = """
    SELECT jid,
           array_agg(DISTINCT host ORDER BY host) AS hosts,
           min(time) AS t0,
           max(time) AS t1,
           count(DISTINCT time) AS nt
    FROM host_data
    WHERE jid IS NOT NULL AND jid <> '-'
    GROUP BY jid
  """
  with connection.cursor() as cursor:
    cursor.execute(sql)
    rows = cursor.fetchall()
  created = 0
  skipped = 0
  existing = set(
    job_data.objects.filter(jid__in=[r[0] for r in rows]).values_list(
      "jid", flat=True
    )
  )
  for jid, hosts, t0, t1, nt in rows:
    if jid in existing or int(nt) < int(args.min_samples):
      skipped += 1
      continue
    hosts = [str(h) for h in hosts if h]
    nhosts = len(hosts)
    cores = sum(_cores_per_host(h) for h in hosts)
    end = t1 if t1 > t0 else t0 + timedelta(seconds=60)
    runtime = (end - t0).total_seconds()
    queue = (
      hosts[0].split(".", 1)[1].split(".")[0] if "." in hosts[0] else "corpus"
    )
    job_data.objects.create(
      jid=str(jid),
      submit_time=t0,
      start_time=t0,
      end_time=end,
      runtime=runtime,
      timelimit=runtime,
      node_hrs=nhosts * runtime / 3600.0,
      nhosts=nhosts,
      ncores=cores,
      username=str(args.username),
      account="corpus",
      queue=queue,
      state="COMPLETED",
      host_list=hosts,
    )
    created += 1
    print(
      f"seed-job-data: jid={jid} hosts={nhosts} cores={cores} "
      f"samples={nt} start={t0.isoformat()} end={end.isoformat()}"
    )
  print(f"seed-job-data: created={created} skipped={skipped}")
  return 0


def _json_safe(value: Any) -> Any:
  """
  Convert NumPy scalars, NaN, datetimes and containers to JSON-safe values.

  NaN and infinities become None; tz-aware datetimes become epoch
  microseconds (int); numpy scalars become Python scalars.

  Args:
    value (Any): Value to convert (scalar, list, tuple, dict, numpy array).

  Returns:
    Any: JSON-serialisable equivalent.

  Examples:
    >>> _json_safe([float("nan"), 1.5, (1, 2)])
    [None, 1.5, [1, 2]]
  """
  import math
  from datetime import datetime

  import numpy as np

  if value is None:
    return None
  if isinstance(value, dict):
    return {str(k): _json_safe(v) for k, v in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_safe(v) for v in value]
  if isinstance(value, np.ndarray):
    return [_json_safe(v) for v in value.tolist()]
  if isinstance(value, (np.generic,)):
    value = value.item()
  if isinstance(value, datetime):
    if value.tzinfo is None:
      from datetime import UTC

      value = value.replace(tzinfo=UTC)
    return round(value.timestamp() * 1_000_000)
  if hasattr(value, "to_pydatetime"):
    return _json_safe(value.to_pydatetime())
  if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
    return None
  if isinstance(value, (bool, int, float, str)):
    return value
  if isinstance(value, bytes):
    return value.decode("utf-8", "replace")
  return str(value)


def _frame_columns(df: Any) -> dict[str, list[Any]]:
  """
  Serialise a pandas DataFrame as ``{column: list}`` with JSON-safe cells.

  Datetime columns become epoch microseconds; NaN / empty-string cells
  become None.

  Args:
    df (Any): DataFrame (or None / empty).

  Returns:
    dict[str, list[Any]]: Column lists (empty dict for None/empty input).

  Examples:
    >>> _frame_columns(None)
    {}
  """
  if df is None or getattr(df, "empty", True):
    return {}
  out: dict[str, list[Any]] = {}
  for col in df.columns:
    series = df[col]
    if str(series.dtype).startswith("datetime64"):
      vals = [
        None
        if ts is None or str(ts) == "NaT"
        else _json_safe(ts.to_pydatetime())
        for ts in series
      ]
    else:
      vals = [
        None if (isinstance(v, str) and v == "") else _json_safe(v)
        for v in series.tolist()
      ]
    out[str(col)] = vals
  return out


def _save_frame_npz(path: Path, frame: Any) -> None:
  """
  Save a JobFrame's arrays and name tables as a compressed ``.npz``.

  Args:
    path (Path): Output path.
    frame (Any): ``JobFrame``.

  Returns:
    None

  Examples:
    >>> _save_frame_npz(Path("/tmp/x.npz"), frame)  # doctest: +SKIP
  """
  import numpy as np

  np.savez_compressed(
    path,
    jid=np.array(frame.jid),
    time_us=frame.time_us,
    host_id=frame.host_id,
    series_id=frame.series_id,
    dev_id=frame.dev_id,
    owned=frame.owned,
    value=frame.value,
    arc=frame.arc,
    delta=frame.delta,
    hosts=np.array(list(frame.hosts), dtype=object),
    series_type=np.array([t for t, _e in frame.series], dtype=object),
    series_event=np.array([e for _t, e in frame.series], dtype=object),
    devs=np.array(list(frame.devs), dtype=object),
  )


def load_frame_npz(path: Path) -> Any:
  """
  Load a JobFrame saved by ``_save_frame_npz``.

  Args:
    path (Path): ``.npz`` path.

  Returns:
    Any: ``JobFrame`` instance.

  Examples:
    >>> load_frame_npz(Path("/tmp/x.npz"))  # doctest: +SKIP
  """
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame

  with np.load(path, allow_pickle=True) as z:
    return JobFrame(
      jid=str(z["jid"]),
      time_us=z["time_us"],
      host_id=z["host_id"],
      series_id=z["series_id"],
      dev_id=z["dev_id"],
      owned=z["owned"],
      value=z["value"],
      arc=z["arc"],
      delta=z["delta"],
      hosts=[str(h) for h in z["hosts"].tolist()],
      series=list(
        zip(
          [str(t) for t in z["series_type"].tolist()],
          [str(e) for e in z["series_event"].tolist()],
          strict=True,
        )
      ),
      devs=[str(d) for d in z["devs"].tolist()],
    )


def _load_detail_payloads(jid: str) -> dict[str, dict[str, Any]]:
  """
  Decompress every ``job_detail_artifact`` row of a job.

  Args:
    jid (str): Job id.

  Returns:
    dict[str, dict[str, Any]]: ``kind -> scope -> {fingerprint, payload}``.

  Examples:
    >>> _load_detail_payloads("x")  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.job_detail_artifacts import (
    _decompress_payload,
  )
  from hpcperfstats.site.lib.machine.models import job_detail_artifact

  out: dict[str, dict[str, Any]] = {}
  for row in job_detail_artifact.objects.filter(jid_id=jid):
    out.setdefault(row.artifact_kind, {})[row.artifact_scope or ""] = {
      "fingerprint": row.input_fingerprint,
      "payload": _decompress_payload(
        bytes(row.payload_compressed), row.payload_encoding
      ),
    }
  return out


def _load_plot_payloads(jid: str) -> dict[str, dict[str, Any]]:
  """
  Decompress every ``job_plot_artifact`` row of a job.

  Args:
    jid (str): Job id.

  Returns:
    dict[str, dict[str, Any]]: ``plot_kind -> layout -> {fingerprint,
    item}`` where ``item`` is the stored ``plot_item`` /
    ``unavailable_reason`` dict.

  Examples:
    >>> _load_plot_payloads("x")  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    decompress_plot_item_dict,
  )
  from hpcperfstats.site.lib.machine.models import job_plot_artifact

  out: dict[str, dict[str, Any]] = {}
  for row in job_plot_artifact.objects.filter(jid_id=jid):
    out.setdefault(row.plot_kind, {})[row.layout] = {
      "fingerprint": row.input_fingerprint,
      "item": decompress_plot_item_dict(
        bytes(row.payload_compressed), row.payload_encoding
      ),
    }
  return out


def _capture_plot_series(jt: Any) -> dict[str, Any]:
  """
  Re-derive the pre-Bokeh plot series for Summary, rooflines and type detail.

  Uses the same ``_JtMemoProxy`` / ``SummaryPlot`` / roofline helpers as
  prewarm; aggregates are served from the ``jid_table`` cache filled by the
  artifact build.

  Args:
    jt (Any): ``jid_table`` of the job.

  Returns:
    dict[str, Any]: ``summary``, ``roofline``, ``gpu_roofline``,
    ``type_detail`` sections with column lists and reasons.

  Examples:
    >>> _capture_plot_series(jt)  # doctest: +SKIP
  """
  import hpcperfstats.analysis.metrics.lib.gen.jid_table as jid_table_mod
  from hpcperfstats.analysis.metrics.lib.plot import roofline as rf
  from hpcperfstats.analysis.metrics.lib.plot.devplot import DevPlot
  from hpcperfstats.analysis.metrics.lib.plot.summaryplot import (
    SummaryPlot,
    serial_summary_aggregate_prefetch_context,
  )
  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    COMMON_PLOT_AGGREGATE_BUNDLE,
    _JtMemoProxy,
  )

  proxy = _JtMemoProxy(jt)
  with contextlib.suppress(Exception):
    proxy.prefetch_aggregate_bundle(COMMON_PLOT_AGGREGATE_BUNDLE)
  out: dict[str, Any] = {}
  summary = SummaryPlot(proxy)
  summary_reason = None
  with serial_summary_aggregate_prefetch_context():
    try:
      summary.plot()
    except Exception as exc:
      summary_reason = str(exc)
  out["summary"] = {
    "columns": _frame_columns(summary.last_frame),
    "render_specs": _json_safe(summary.last_render_specs),
    "reason": summary_reason,
  }
  cpu_df, cpu_reason = rf._get_flops_bw_df_and_reason(proxy)
  cpu_peaks = rf.infer_cpu_roofline_peak_flops_and_bw_gbps(proxy)
  out["roofline"] = {
    "columns": _frame_columns(cpu_df),
    "reason": cpu_reason,
    "peaks": _json_safe(list(cpu_peaks)),
  }
  gpu_df, gpu_reason, bw_axis = rf._get_gpu_flops_bw_df_and_reason(proxy)
  gpu_peaks = rf.infer_gpu_roofline_peak_flops_and_bw_gbps(
    proxy, bw_axis=bw_axis
  )
  out["gpu_roofline"] = {
    "columns": _frame_columns(gpu_df),
    "reason": gpu_reason,
    "bw_axis": bw_axis,
    "peaks": _json_safe(list(gpu_peaks)),
  }
  type_detail: dict[str, Any] = {}
  for type_name in sorted((jt.schema or {}).keys()):
    provider = jid_table_mod.TypeDetailDataProvider(
      jt.jid, type_name, jt.start_time, jt.end_time, jt.acct_host_list
    )
    try:
      df, _comp = DevPlot(provider, jt.acct_host_list).plot()
      type_detail[type_name] = {
        "columns": _frame_columns(df),
        "events_units": _json_safe(provider.get_events_units()),
        "type_list": _json_safe(provider.get_type_list()),
        "error": None,
      }
    except Exception as exc:
      type_detail[type_name] = {"columns": {}, "error": str(exc)}
  out["type_detail"] = type_detail
  return out


def capture_job(
  jid: str, corpus_dir: Path, edge_cases: list[str]
) -> dict[str, Any]:
  """
  Run the pandas path for one job and write its golden fixture files.

  Steps: fetch the JobFrame (saved as ``jobs/<jid>.frame.npz``), compute and
  persist metrics with the production worker (``_unwrap_and_persist``),
  rebuild detail and plot artifacts from scratch, then save metrics rows,
  detail payloads, plot JSON and pre-Bokeh series as
  ``jobs/<jid>.golden.json.gz``.

  Args:
    jid (str): Job id.
    corpus_dir (Path): Corpus directory.
    edge_cases (list[str]): Edge-case tags for the manifest.

  Returns:
    dict[str, Any]: Manifest row for the job.

  Examples:
    >>> capture_job("1", Path("/tmp/corpus"), [])  # doctest: +SKIP
  """
  import gzip

  from django.core.cache import cache

  import hpcperfstats.analysis.metrics.lib.gen.jid_table as jid_table_mod
  from hpcperfstats.analysis.metrics import update_metrics as um
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.lib.columnar.copy_fetch import (
    fetch_job_frame,
  )
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    gpu_devs_for_job,
    job_is_shared_node,
  )
  from hpcperfstats.site.lib.machine.models import (
    job_data,
    job_detail_artifact,
    job_plot_artifact,
  )

  cache.clear()
  job_plot_artifact.objects.filter(jid_id=jid).delete()
  job_detail_artifact.objects.filter(jid_id=jid).delete()
  job_row = job_data.objects.filter(jid=jid).values().first() or {}
  ref = um._candidate_ref(jid)
  metrics_obj = metrics.Metrics()
  payload = metrics._unwrap((metrics_obj, ref))
  outcome = metrics._persist_metrics_payload(dict(payload))
  shared_ctx: dict[str, Any] = {}
  um._PrewarmPipeline()._persist_detail_plot_elapsed(jid, shared_ctx)
  jt = shared_ctx.get("jt") or jid_table_mod.jid_table(jid)
  frame = fetch_job_frame(jt)
  frame.check_contract()
  jobs_dir = corpus_dir / "jobs"
  jobs_dir.mkdir(parents=True, exist_ok=True)
  frame_path = jobs_dir / f"{jid}.frame.npz"
  _save_frame_npz(frame_path, frame)
  rows = [
    {k: _json_safe(v) for k, v in row.items() if k != "jid"}
    for row in (payload.get("rows") or [])
  ]
  rows.sort(key=lambda r: (str(r.get("type")), str(r.get("metric"))))
  golden = {
    "jid": jid,
    "job": _json_safe(job_row),
    "jid_table": {
      "host_list": list(jt.host_list),
      "acct_host_list": list(jt.acct_host_list),
      "schema": _json_safe(jt.schema),
      "start_time": _json_safe(jt.start_time),
      "end_time": _json_safe(jt.end_time),
      "time_in": _json_safe(list(jt._base_filter.get("time__in") or []))
      if "time__in" in (jt._base_filter or {})
      else None,
      "shared_node": bool(job_is_shared_node(str(jid))),
      "gpu_affinity_devs": _json_safe(gpu_devs_for_job(str(jid))),
    },
    "metrics": {
      "status": payload.get("status"),
      "rows": rows,
      "distinct_time_count": _json_safe(payload.get("distinct_time_count")),
      "telemetry_first_time": _json_safe(payload.get("telemetry_first_time")),
      "telemetry_last_time": _json_safe(payload.get("telemetry_last_time")),
      "host_data_schema": _json_safe(payload.get("host_data_schema")),
      "persist_outcome": _json_safe(outcome),
    },
    "detail": _json_safe(_load_detail_payloads(jid)),
    "plots": _json_safe(_load_plot_payloads(jid)),
    "series": _json_safe(_capture_plot_series(jt)),
  }
  golden_path = jobs_dir / f"{jid}.golden.json.gz"
  with gzip.open(golden_path, "wt", encoding="utf-8") as fh:
    json.dump(golden, fh, separators=(",", ":"))
  return {
    "jid": jid,
    "fixture": str(golden_path.relative_to(corpus_dir)),
    "frame": str(frame_path.relative_to(corpus_dir)),
    "edge_cases": list(edge_cases),
    "n_rows": len(frame),
    "n_hosts": len(frame.hosts),
    "n_series": len(frame.series),
    "metrics_status": payload.get("status"),
    "metrics_rows": len(rows),
    "plot_kinds": sorted((golden["plots"] or {}).keys()),
    "detail_kinds": sorted((golden["detail"] or {}).keys()),
  }


def pandas_path_commit() -> str:
  """
  Commit hash of the pandas path that produced the corpus.

  Reads ``HPCPERFSTATS_GOLDEN_COMMIT`` first (the dev container has no
  ``git``), then ``git rev-parse HEAD``; ``unknown`` when neither works.

  Returns:
    str: Commit hash or ``unknown``.

  Examples:
    >>> import os
    >>> os.environ["HPCPERFSTATS_GOLDEN_COMMIT"] = "abc123"
    >>> pandas_path_commit()
    'abc123'
    >>> del os.environ["HPCPERFSTATS_GOLDEN_COMMIT"]
  """
  env = os.environ.get("HPCPERFSTATS_GOLDEN_COMMIT", "").strip()
  if env:
    return env
  try:
    out = subprocess.run(
      ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
  except OSError, subprocess.SubprocessError:
    return "unknown"
  return out or "unknown"


def cmd_capture(args: argparse.Namespace) -> int:
  """
  Capture golden fixtures for every corpus job and write ``manifest.json``.

  Args:
    args (argparse.Namespace): ``dir``, ``username``, ``jids``.

  Returns:
    int: 0 when every job captured, 1 when any job failed.

  Examples:
    >>> cmd_capture(
    ...   argparse.Namespace(  # doctest: +SKIP
    ...     dir=None, username="corpus", jids=None
    ...   )
    ... )
  """

  _ensure_django()
  from hpcperfstats.site.lib.machine.models import job_data

  corpus_dir = _corpus_dir(args.dir)
  corpus_dir.mkdir(parents=True, exist_ok=True)
  edge_path = corpus_dir / "edge_jobs.json"
  edge_tags: dict[str, list[str]] = (
    json.loads(edge_path.read_text()) if edge_path.is_file() else {}
  )
  if args.jids:
    jids = [j.strip() for j in str(args.jids).split(",") if j.strip()]
  else:
    jids = [
      str(j)
      for j in job_data.objects.filter(username=str(args.username))
      .order_by("start_time")
      .values_list("jid", flat=True)
    ]
  manifest: dict[str, Any] = {
    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "pandas_path_commit": pandas_path_commit(),
    "jobs": [],
    "failed": [],
  }
  if args.jids:
    # Partial re-capture: keep rows of jobs not captured in this run.
    previous = _read_manifest(corpus_dir)
    manifest["jobs"] = [
      row for row in previous.get("jobs", []) if str(row.get("jid")) not in jids
    ]
    manifest["failed"] = [
      row
      for row in previous.get("failed", [])
      if str(row.get("jid")) not in jids
    ]
  for index, jid in enumerate(jids):
    t0 = time.monotonic()
    try:
      row = capture_job(jid, corpus_dir, edge_tags.get(jid, []))
    except Exception as exc:
      print(f"capture: [{index + 1}/{len(jids)}] jid={jid} FAILED {exc!r}")
      manifest["failed"].append({"jid": jid, "error": repr(exc)})
      continue
    manifest["jobs"].append(row)
    print(
      f"capture: [{index + 1}/{len(jids)}] jid={jid} rows={row['n_rows']} "
      f"series={row['n_series']} metrics={row['metrics_rows']} "
      f"elapsed_s={time.monotonic() - t0:.1f}",
      flush=True,
    )
  (corpus_dir / "manifest.json").write_text(
    json.dumps(manifest, indent=2), encoding="utf-8"
  )
  print(
    f"capture: jobs={len(manifest['jobs'])} failed={len(manifest['failed'])} "
    f"manifest={corpus_dir / 'manifest.json'}"
  )
  return 1 if manifest["failed"] else 0


EDGE_BASE_EPOCH = 1_790_000_000.0  # 2026-09-25T14:13:20Z
EDGE_SAMPLE_INTERVAL_S = 60
DCGM_FP64_BLANK_TEXT = "140737488355328.0"


def _edge_host(case_idx: int, n: int) -> str:
  """
  Short synthetic host name for an edge case, under the dev INI suffix.

  Args:
    case_idx (int): Edge case ordinal.
    n (int): Host ordinal within the case.

  Returns:
    str: ``ec<case>-<n>.<host_name_ext>``.

  Examples:
    >>> _edge_host(1, 2)  # doctest: +SKIP
    'ec01-002.cluster_name.domain.edu'
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  ext = str(cfg.get_host_name_ext() or "").strip().lstrip(".")
  return f"ec{case_idx:02d}-{n:03d}.{ext}"


def _patch_gpu_fields(snapshot: str, field_values: dict[int, str]) -> str:
  """
  Replace numbered value fields on the ``nvidia_gpu 0 @fast/@full`` lines.

  Field 0 is the first value after the ``@fast`` / ``@full`` tier token.

  Args:
    snapshot (str): One timestamp block from ``full_stats_snapshot``.
    field_values (dict[int, str]): Field index -> replacement text.

  Returns:
    str: Patched snapshot text.

  Examples:
    >>> _patch_gpu_fields("nvidia_gpu 0 @fast 1 2 3\\n", {1: "9"})
    'nvidia_gpu 0 @fast 1 9 3\\n'
  """
  out = []
  for line in snapshot.splitlines(keepends=True):
    if line.startswith("nvidia_gpu 0 @"):
      parts = line.rstrip("\n").split(" ")
      for idx, text in field_values.items():
        pos = 3 + idx
        if pos < len(parts):
          parts[pos] = text
      line = " ".join(parts) + "\n"
    out.append(line)
  return "".join(out)


def _edge_stats_text(
  jid: str,
  fqdn: str,
  scales: list[int],
  *,
  marks: list[str] | None = None,
  gpu_patch: dict[int, dict[int, str]] | None = None,
  epoch0: float = EDGE_BASE_EPOCH,
  epoch_offset_s: float = 0.0,
) -> str:
  """
  Build a synthetic raw stats file (schema header plus one block per sample).

  Args:
    jid (str): Job id written into every sample header.
    fqdn (str): Host FQDN.
    scales (list[int]): Generator ``scale`` per sample (counters grow with
      scale, so a drop produces a counter rollover).
    marks (list[str] | None): ``%jobs`` / ``%affinity`` lines placed before
      the first sample header.
    gpu_patch (dict[int, dict[int, str]] | None): Sample index -> GPU field
      patches (see ``_patch_gpu_fields``).
    epoch0 (float): Epoch of the first sample.
    epoch_offset_s (float): Added to every sample epoch.

  Returns:
    str: Stats file text.

  Examples:
    >>> text = _edge_stats_text("j", "h.x", [0, 800])  # doctest: +SKIP
  """
  from tests.pipeline_e2e.monitor_payloads import (
    full_stats_snapshot,
    monitor_schema_header,
  )

  parts = [monitor_schema_header(fqdn)]
  parts.extend(f"{m}\n" for m in (marks or []))
  for i, scale in enumerate(scales):
    epoch = epoch0 + epoch_offset_s + i * EDGE_SAMPLE_INTERVAL_S
    snap = full_stats_snapshot(epoch, jid, fqdn, scale)
    if gpu_patch and i in gpu_patch:
      snap = _patch_gpu_fields(snap, gpu_patch[i])
    parts.append(snap)
  return "".join(parts)


def _ingest_text(
  st: Any, work_dir: Path, fqdn: str, epoch: float, text: str
) -> bool:
  """
  Write ``text`` as ``<work_dir>/<fqdn>/<epoch>`` and ingest it.

  Args:
    st (Any): ``hpcperfstats.dbload.sync_timedb`` module.
    work_dir (Path): Staging directory.
    fqdn (str): Host directory name.
    epoch (float): File name (integer epoch).
    text (str): Stats file content.

  Returns:
    bool: Ingest succeeded.

  Examples:
    >>> _ingest_text(st, Path("/tmp"), "h", 1.0, "")  # doctest: +SKIP
  """
  host_dir = work_dir / fqdn
  host_dir.mkdir(parents=True, exist_ok=True)
  path = host_dir / str(int(epoch))
  path.write_text(text, encoding="utf-8")
  result = st.add_stats_file_to_db(str(path))
  ok = (
    bool(result[2]) if isinstance(result, tuple) and len(result) > 2 else False
  )
  print(f"seed-edge-jobs: ingest path={path} ok={int(ok)}", flush=True)
  return ok


EDGE_CASE_TAGS: dict[str, list[str]] = {
  "edge_small": ["small_job"],
  "edge_single": ["single_sample"],
  "edge_rollover": ["counter_rollover"],
  "edge_gpupoison": ["gpu_poison"],
  "edge_missinghost": ["missing_host"],
  "edge_multihost": ["multi_host"],
  "edge_nodata": ["no_data"],
  "edge_shared_a": ["shared_node"],
  "edge_shared_b": ["shared_node"],
}


def cmd_tag_edge_jobs(args: argparse.Namespace) -> int:
  """
  Write ``edge_jobs.json`` from the static edge-case table without ingesting.

  Used when the file was lost (for example written inside an unmounted
  container) while the jobs already exist in the database.

  Args:
    args (argparse.Namespace): ``--dir``.

  Returns:
    int: Exit status (0).

  Examples:
    >>> cmd_tag_edge_jobs(argparse.Namespace(dir="/tmp/x"))  # doctest: +SKIP
    0
  """
  corpus_dir = _corpus_dir(args.dir)
  corpus_dir.mkdir(parents=True, exist_ok=True)
  (corpus_dir / "edge_jobs.json").write_text(
    json.dumps(EDGE_CASE_TAGS, indent=2, sort_keys=True), encoding="utf-8"
  )
  print(f"tag-edge-jobs: wrote {corpus_dir / 'edge_jobs.json'}")
  return 0


def cmd_seed_edge_jobs(args: argparse.Namespace) -> int:
  """
  Ingest synthetic edge-case jobs and record their tags in ``edge_jobs.json``.

  Cases: ``small_job`` (3 samples), ``single_sample``, ``counter_rollover``
  (counters drop mid-job), ``gpu_poison`` (DCGM blank power, 2^63 link
  bytes), ``missing_host`` (host_list host without rows), ``multi_host``
  (4 hosts), ``shared_node`` (two jobs on one host with ``%jobs`` /
  ``%affinity`` marks and ``j:`` copies) and ``no_data`` (job_data only).
  Existing edge jobs are deleted and recreated.

  Args:
    args (argparse.Namespace): ``dir``, ``username``, ``work_dir``.

  Returns:
    int: 0 on success, 1 when any ingest failed.

  Examples:
    >>> cmd_seed_edge_jobs(
    ...   argparse.Namespace(  # doctest: +SKIP
    ...     dir=None, username="corpus", work_dir="/tmp/edge"
    ...   )
    ... )
  """
  _ensure_django()
  from datetime import UTC, datetime, timedelta

  from django.db import connection

  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.site.lib.machine.models import job_affinity, job_data

  st.should_archive = False
  corpus_dir = _corpus_dir(args.dir)
  corpus_dir.mkdir(parents=True, exist_ok=True)
  work_dir = Path(args.work_dir)
  work_dir.mkdir(parents=True, exist_ok=True)
  ramp = [i * 800 for i in range(40)]
  cases: list[dict[str, Any]] = [
    {
      "jid": "edge_small",
      "tags": ["small_job"],
      "hosts": 1,
      "scales": ramp[:3],
    },
    {
      "jid": "edge_single",
      "tags": ["single_sample"],
      "hosts": 1,
      "scales": ramp[:1],
    },
    {
      "jid": "edge_rollover",
      "tags": ["counter_rollover"],
      "hosts": 1,
      "scales": ramp[:10] + [s // 8 for s in ramp[1:11]],
    },
    {
      "jid": "edge_gpupoison",
      "tags": ["gpu_poison"],
      "hosts": 1,
      "scales": ramp[:12],
      "gpu_patch": {
        3: {9: DCGM_FP64_BLANK_TEXT},
        4: {9: DCGM_FP64_BLANK_TEXT},
        7: {10: f"{float(2**63):.0f}"},
      },
    },
    {
      "jid": "edge_missinghost",
      "tags": ["missing_host"],
      "hosts": 1,
      "extra_hosts": 1,
      "scales": ramp[:10],
    },
    {
      "jid": "edge_multihost",
      "tags": ["multi_host"],
      "hosts": 4,
      "scales": ramp[:30],
    },
    {"jid": "edge_nodata", "tags": ["no_data"], "hosts": 1, "scales": []},
  ]
  all_jids = [c["jid"] for c in cases] + ["edge_shared_a", "edge_shared_b"]
  job_data.objects.filter(jid__in=all_jids).delete()
  job_affinity.objects.filter(jid__in=all_jids).delete()
  with connection.cursor() as cursor:
    cursor.execute(
      "DELETE FROM host_data WHERE jid = ANY(%s::text[])", [all_jids]
    )
    cursor.execute("DELETE FROM host_data WHERE host LIKE 'ec%%'")
  failed = 0
  tags: dict[str, list[str]] = {}
  t0 = datetime.fromtimestamp(EDGE_BASE_EPOCH, tz=UTC)
  for idx, case in enumerate(cases, start=1):
    hosts = [_edge_host(idx, n) for n in range(1, int(case["hosts"]) + 1)]
    for h_idx, fqdn in enumerate(hosts):
      if not case["scales"]:
        continue
      text = _edge_stats_text(
        case["jid"],
        fqdn,
        [s + h_idx * 50 for s in case["scales"]],
        gpu_patch=case.get("gpu_patch"),
      )
      if not _ingest_text(st, work_dir, fqdn, EDGE_BASE_EPOCH, text):
        failed += 1
    host_list = hosts + [
      _edge_host(idx, 90 + n) for n in range(int(case.get("extra_hosts", 0)))
    ]
    n_samples = max(1, len(case["scales"]))
    end = t0 + timedelta(seconds=EDGE_SAMPLE_INTERVAL_S * max(0, n_samples - 1))
    if end <= t0:
      end = t0 + timedelta(seconds=EDGE_SAMPLE_INTERVAL_S)
    job_data.objects.create(
      jid=case["jid"],
      submit_time=t0,
      start_time=t0,
      end_time=end,
      runtime=(end - t0).total_seconds(),
      timelimit=(end - t0).total_seconds(),
      node_hrs=len(host_list) * (end - t0).total_seconds() / 3600.0,
      nhosts=len(host_list),
      ncores=64 * len(host_list),
      username=str(args.username),
      account="corpus",
      queue="edge",
      state="COMPLETED",
      host_list=host_list,
    )
    tags[case["jid"]] = list(case["tags"])
  # Shared node: two jobs on one host, j: copies and affinity rows.
  shared_host = _edge_host(len(cases) + 1, 1)
  marks_a = [
    "%jobs jids=edge_shared_a,edge_shared_b",
    "%affinity jid=edge_shared_a cpus=0-31 gpus=- cg=hpcperfstats",
    "%affinity jid=edge_shared_b cpus=32-63 gpus=- cg=hpcperfstats",
  ]
  text_a = _edge_stats_text(
    "edge_shared_a", shared_host, ramp[:15], marks=marks_a
  )
  text_b = _edge_stats_text(
    "edge_shared_b",
    shared_host,
    [s + 25 for s in ramp[:15]],
    marks=["%jobs jids=edge_shared_a,edge_shared_b"],
    epoch_offset_s=1.0,
  )
  if not _ingest_text(st, work_dir, shared_host, EDGE_BASE_EPOCH, text_a):
    failed += 1
  if not _ingest_text(st, work_dir, shared_host, EDGE_BASE_EPOCH + 1, text_b):
    failed += 1
  for jid, cpus in (("edge_shared_a", "0-31"), ("edge_shared_b", "32-63")):
    job_affinity.objects.update_or_create(
      jid=jid,
      host=shared_host,
      defaults={
        "cpu_list": [cpus],
        "gpu_list": [],
        "cgroup_controls": {},
      },
    )
    end = t0 + timedelta(seconds=EDGE_SAMPLE_INTERVAL_S * 14 + 1)
    job_data.objects.create(
      jid=jid,
      submit_time=t0,
      start_time=t0,
      end_time=end,
      runtime=(end - t0).total_seconds(),
      timelimit=(end - t0).total_seconds(),
      node_hrs=(end - t0).total_seconds() / 3600.0,
      nhosts=1,
      ncores=32,
      username=str(args.username),
      account="corpus",
      queue="edge",
      state="COMPLETED",
      host_list=[shared_host],
    )
    tags[jid] = ["shared_node"]
  (corpus_dir / "edge_jobs.json").write_text(
    json.dumps(tags, indent=2, sort_keys=True), encoding="utf-8"
  )
  print(f"seed-edge-jobs: jobs={len(tags)} failed_ingests={failed}")
  return 1 if failed else 0


def _read_manifest(corpus_dir: Path) -> dict[str, Any]:
  """
  Load ``manifest.json`` from the corpus directory.

  Args:
    corpus_dir (Path): Corpus directory.

  Returns:
    dict[str, Any]: Parsed manifest, or an empty dict when missing.

  Examples:
    >>> _read_manifest(Path("/nonexistent"))
    {}
  """
  path = corpus_dir / "manifest.json"
  if not path.is_file():
    return {}
  return json.loads(path.read_text(encoding="utf-8"))


def verify_corpus(corpus_dir: Path, min_jobs: int) -> list[str]:
  """
  Check the manifest, job count, edge-case coverage and fixture files.

  Args:
    corpus_dir (Path): Corpus directory.
    min_jobs (int): Minimum number of captured jobs.

  Returns:
    list[str]: Problems found (empty when the corpus verifies).

  Examples:
    >>> verify_corpus(Path("/nonexistent"), 1)
    ['manifest.json missing under /nonexistent']
  """
  manifest = _read_manifest(corpus_dir)
  if not manifest:
    return [f"manifest.json missing under {corpus_dir}"]
  problems: list[str] = []
  jobs = manifest.get("jobs") or []
  if len(jobs) < min_jobs:
    problems.append(f"only {len(jobs)} jobs captured; need >= {min_jobs}")
  edge_path = corpus_dir / "edge_jobs.json"
  edge_tags: dict[str, list[str]] = (
    json.loads(edge_path.read_text(encoding="utf-8"))
    if edge_path.is_file()
    else {}
  )
  tags: set[str] = set()
  for job in jobs:
    tags.update(job.get("edge_cases") or [])
    tags.update(edge_tags.get(str(job.get("jid")), []))
    fixture = corpus_dir / str(job.get("fixture") or "")
    if not fixture.is_file():
      problems.append(f"fixture missing for jid={job.get('jid')}: {fixture}")
  for tag in REQUIRED_EDGE_CASES:
    if tag not in tags:
      problems.append(f"edge case not covered: {tag}")
  return problems


def cmd_verify(args: argparse.Namespace) -> int:
  """
  Verify the corpus and print the gate marker on success.

  Args:
    args (argparse.Namespace): ``dir``, ``min_jobs``.

  Returns:
    int: 0 when the corpus verifies, 1 otherwise.

  Examples:
    >>> cmd_verify(argparse.Namespace(dir="/nonexistent", min_jobs=1))
    verify: manifest.json missing under /nonexistent
    1
  """
  problems = verify_corpus(_corpus_dir(args.dir), int(args.min_jobs))
  for problem in problems:
    print(f"verify: {problem}")
  if problems:
    return 1
  print("GOLDEN_CORPUS_GATE_OK")
  return 0


def build_parser() -> argparse.ArgumentParser:
  """
  Build the CLI parser with the corpus subcommands.

  Returns:
    argparse.ArgumentParser: Configured parser.

  Examples:
    >>> build_parser().parse_args(["verify", "--min-jobs", "3"]).min_jobs
    '3'
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  parser.add_argument("--dir", help="corpus directory (outside git)")
  sub = parser.add_subparsers(dest="command", required=True)

  seed = sub.add_parser("seed-exemplars", help="ingest raw stats files")
  seed.add_argument("--raw-root", required=True)
  seed.add_argument("--max-bytes", default=500_000_000)
  seed.add_argument("--min-samples", default=MIN_SAMPLES_PER_JOB)
  seed.add_argument("--limit", default=0, type=int)
  seed.set_defaults(func=cmd_seed_exemplars)

  jobs = sub.add_parser("seed-job-data", help="synthesize job_data rows")
  jobs.add_argument("--min-samples", default=MIN_SAMPLES_PER_JOB)
  jobs.add_argument("--username", default="corpus")
  jobs.set_defaults(func=cmd_seed_job_data)

  rewrite = sub.add_parser(
    "rewrite-host-suffix", help="rename hosts to the INI host_name_ext"
  )
  rewrite.add_argument("--username", default="corpus")
  rewrite.set_defaults(func=cmd_rewrite_host_suffix)

  edge = sub.add_parser("seed-edge-jobs", help="ingest synthetic edge jobs")
  edge.add_argument("--username", default="corpus")
  edge.add_argument(
    "--work-dir", default="/tmp/hpcperfstats-edge-jobs", help="staging dir"
  )
  edge.set_defaults(func=cmd_seed_edge_jobs)

  tag = sub.add_parser("tag-edge-jobs", help="write edge_jobs.json only")
  tag.set_defaults(func=cmd_tag_edge_jobs)
  capture = sub.add_parser("capture", help="capture pandas-path fixtures")
  capture.add_argument("--username", default="corpus")
  capture.add_argument("--jids", help="comma-separated jids (default all)")
  capture.set_defaults(func=cmd_capture)

  verify = sub.add_parser("verify", help="verify manifest and fixtures")
  verify.add_argument("--min-jobs", default=30)
  verify.set_defaults(func=cmd_verify)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry point.

  Args:
    argv (Sequence[str] | None): Arguments without the program name, or None
      for ``sys.argv[1:]``.

  Returns:
    int: Process exit code.

  Examples:
    >>> main(["--dir", "/nonexistent", "verify"])
    verify: manifest.json missing under /nonexistent
    1
  """
  args = build_parser().parse_args(argv)
  return int(args.func(args))


if __name__ == "__main__":
  sys.exit(main())
