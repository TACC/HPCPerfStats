"""
Admin Monitor section: ingest vs Job Detail coverage (Valkey reads only).

Compares the ``(type, event)`` pairs each host writes to ``host_data`` (the
``ingest_coverage_sweeper`` records, ``hpcperfstats.dbload.lib.ingest_coverage``)
with the pairs update_metrics reads for Job Detail (``metrics_consumed:v1``
hashes written by ``columnar.consumed_series``), then groups hosts by gap
signature (default), rack, schema variant or capability slug.

This module never queries the database: a refresh costs a few Valkey round
trips (MGET in chunks, one HGETALL per stage). A missing, failed or stale
scan is reported (``incomplete`` / ``sweeper_stale`` / ``sweep_disabled``),
never as "nothing missing".

Pair classes (union over every recent job, ``CONSUMED_FRESH_DAYS``):

* ``derived`` — read by ``metrics``, ``summary_plot``, ``job_detail`` or an
  untagged path (``other``);
* ``type_detail_only`` — read only by the per-type panel;
* ``type_detail_dropped`` — last seen dropped from the per-type panel (a
  sample was missing) and never derived;
* ``unused`` — never read.

Attributes:
  CONSUMED_FRESH_DAYS: Freshness window for consumed pairs (user, 7 days).
  GROUP_BY_MODES: Accepted ``group_by`` values.
  DERIVED_STAGES: Stages that make a pair ``derived``.
  GAP_CLASSES: Classes that form a host's gap set.
  MGET_CHUNK: Keys per MGET.
  MAX_GROUPS: Groups returned.
  MAX_PAIRS_PER_GROUP: Pair rows per group.
  MAX_HOSTS_PER_GROUP: Host drilldown rows per group.
  MAX_OUTLIERS_PER_GROUP: Outlier host rows per group.
  MAX_CONSUMED_NOT_INGESTED: Reverse-direction rows per group.
  STALE_INTERVALS: Heartbeat age, in sweep intervals, that means stale.
  STATUS_KEYS: Status counters in display order.
  logger: Module logger.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.core.cache import cache

from hpcperfstats.analysis.metrics.lib.columnar.consumed_series import (
  STAGE_OTHER,
  STAGE_TYPE_DETAIL_DROPPED,
  STAGES,
  parse_pair_field,
  stage_key,
)
from hpcperfstats.dbload.lib import conf_parser as cfg, ingest_coverage as ic
from hpcperfstats.lib.monitor_identity import monitor_identity_valkey_key
from hpcperfstats.site.lib.machine.cache_utils import (
  KEY_ADMIN_INGEST_COVERAGE,
  TIMEOUT_ADMIN_INGEST_COVERAGE,
  get_valkey_client,
)
from hpcperfstats.site.lib.machine.host_data_latest import (
  list_recent_host_fqdns_from_valkey,
)

logger = logging.getLogger(__name__)

CONSUMED_FRESH_DAYS = 7
GROUP_BY_MODES = ("signature", "rack", "schema", "slug")
DERIVED_STAGES = ("metrics", "summary_plot", "job_detail", STAGE_OTHER)
GAP_CLASSES = ("unused", "type_detail_dropped")
MGET_CHUNK = 500
MAX_GROUPS = 50
MAX_PAIRS_PER_GROUP = 600
MAX_HOSTS_PER_GROUP = 60
MAX_OUTLIERS_PER_GROUP = 20
MAX_CONSUMED_NOT_INGESTED = 200
STALE_INTERVALS = 2
STATUS_KEYS = (
  ic.STATUS_CURRENT,
  ic.STATUS_INGEST_BEHIND,
  ic.STATUS_UNKNOWN,
  ic.STATUS_INCOMPLETE,
  ic.STATUS_NO_RECENT_DATA,
  "not_scanned",
)


def cache_key_for(group_by: str) -> str:
  """
  Django cache key for one group-by mode.

  Args:
    group_by (str): One of ``GROUP_BY_MODES``.

  Returns:
    str: Cache key.

  Examples:
    >>> cache_key_for("rack")
    'admin_monitor_ingest_coverage:rack'
  """
  return f"{KEY_ADMIN_INGEST_COVERAGE}:{group_by}"


def all_cache_keys() -> list[str]:
  """
  Every cache key the section uses (``refresh=1`` deletes them all).

  Returns:
    list[str]: One key per group-by mode.

  Examples:
    >>> len(all_cache_keys())
    4
  """
  return [cache_key_for(mode) for mode in GROUP_BY_MODES]


def normalize_group_by(value: Any) -> str:
  """
  Return a valid group-by mode (default ``signature``).

  Args:
    value (Any): Query parameter value.

  Returns:
    str: Mode from ``GROUP_BY_MODES``.

  Examples:
    >>> normalize_group_by("RACK"), normalize_group_by("bogus")
    ('rack', 'signature')
  """
  mode = str(value or "").strip().lower()
  return mode if mode in GROUP_BY_MODES else "signature"


def rack_of(fqdn: str) -> str:
  """
  Hostname prefix before the first ``-`` (``c101-001.x`` -> ``c101``).

  Args:
    fqdn (str): Host FQDN.

  Returns:
    str: Rack label (the short hostname when it has no ``-``).

  Examples:
    >>> rack_of("c101-001.horizon.example"), rack_of("login1.example")
    ('c101', 'login1')
  """
  short = str(fqdn).split(".", 1)[0]
  return short.split("-", 1)[0] or short


def schema_variant_of(identity: Mapping[str, Any] | None) -> str:
  """
  Short hash of the host's ``schema_types`` list (``unknown`` when absent).

  Args:
    identity (Mapping[str, Any] | None): ``monitor_identity`` document.

  Returns:
    str: 10-character variant id or ``unknown``.

  Examples:
    >>> schema_variant_of(None)
    'unknown'
    >>> len(schema_variant_of({"schema_types": ["host_cpu"]}))
    10
  """
  types = list((identity or {}).get("schema_types") or [])
  if not types:
    return "unknown"
  return ic.pair_set_signature((t, "") for t in types)[:10]


def classify_pair(stages: Mapping[str, int]) -> str:
  """
  Class of one pair from its fresh ``stage -> last epoch`` map.

  Args:
    stages (Mapping[str, int]): Fresh stages that read the pair.

  Returns:
    str: ``derived``, ``type_detail_only``, ``type_detail_dropped`` or
    ``unused``.

  Examples:
    >>> classify_pair({"metrics": 5})
    'derived'
    >>> classify_pair({"type_detail": 5, "type_detail_dropped": 9})
    'type_detail_dropped'
    >>> classify_pair({})
    'unused'
  """
  if any(s in stages for s in DERIVED_STAGES):
    return "derived"
  kept = stages.get("type_detail")
  dropped = stages.get(STAGE_TYPE_DETAIL_DROPPED)
  if dropped is not None and (kept is None or dropped >= kept):
    return "type_detail_dropped"
  if kept is not None:
    return "type_detail_only"
  return "unused"


def sweeper_state(
  beat: Mapping[str, Any] | None, *, enabled: bool, interval_s: int, now: float
) -> str:
  """
  Liveness of the sweeper from its heartbeat key.

  Args:
    beat (Mapping[str, Any] | None): ``ingest_coverage:v1:sweep`` document.
    enabled (bool): ``ingest_coverage_sweep_enabled``.
    interval_s (int): Fallback interval when the heartbeat lacks one.
    now (float): Epoch seconds.

  Returns:
    str: ``sweep_disabled``, ``sweeper_stale``, ``sweeping`` or ``ok``.

  Examples:
    >>> sweeper_state(None, enabled=False, interval_s=60, now=0)
    'sweep_disabled'
    >>> sweeper_state(None, enabled=True, interval_s=60, now=0)
    'sweeper_stale'
    >>> sweeper_state(
    ...   {"started_at": 90, "finished_at": 50},
    ...   enabled=True,
    ...   interval_s=60,
    ...   now=100,
    ... )
    'sweeping'
  """
  if not enabled:
    return "sweep_disabled"
  if not beat:
    return "sweeper_stale"
  interval = int(beat.get("interval_s") or interval_s or 3600)
  limit = STALE_INTERVALS * interval
  started = int(beat.get("started_at") or 0)
  finished = int(beat.get("finished_at") or 0)
  if started > finished and now - started <= limit:
    return "sweeping"
  if finished and now - finished <= limit:
    return "ok"
  return "sweeper_stale"


def _mget_json(client: Any, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
  """
  MGET ``keys`` in chunks and decode the JSON objects that exist.

  Args:
    client (Any): valkey-py client with ``mget``.
    keys (Sequence[str]): Keys.

  Returns:
    dict[str, dict[str, Any]]: Key to decoded object.

  Examples:
    >>> class _C:
    ...   def mget(self, keys):
    ...     return ['{"a": 1}', None][: len(keys)]
    >>> _mget_json(_C(), ["k1", "k2"])
    {'k1': {'a': 1}}
  """
  out: dict[str, dict[str, Any]] = {}
  for i in range(0, len(keys), MGET_CHUNK):
    chunk = list(keys[i : i + MGET_CHUNK])
    for key, raw in zip(chunk, client.mget(chunk) or [], strict=False):
      doc = ic.decode_json_object(raw)
      if doc is not None:
        out[key] = doc
  return out


def load_consumed(client: Any) -> dict[tuple[str, str], dict[str, int]]:
  """
  Read every ``metrics_consumed:v1:{stage}`` hash (one HGETALL per stage).

  Args:
    client (Any): valkey-py client with ``hgetall``.

  Returns:
    dict[tuple[str, str], dict[str, int]]: Pair to ``stage -> epoch``.

  Examples:
    >>> class _C:
    ...   def hgetall(self, k):
    ...     return {b"host_cpu\\tuser": b"5"} if k.endswith(":metrics") else {}
    >>> load_consumed(_C())
    {('host_cpu', 'user'): {'metrics': 5}}
  """
  out: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
  for stage in STAGES:
    for field, raw in (client.hgetall(stage_key(stage)) or {}).items():
      pair = parse_pair_field(field)
      if pair is None:
        continue
      try:
        out[pair][stage] = int(float(raw))
      except TypeError, ValueError:
        continue
  return dict(out)


def _fresh_consumed(
  consumed: Mapping[tuple[str, str], Mapping[str, int]], now: float
) -> dict[tuple[str, str], dict[str, int]]:
  """
  Keep only stage reads newer than ``CONSUMED_FRESH_DAYS``.

  Args:
    consumed (Mapping[tuple[str, str], Mapping[str, int]]): Raw registry.
    now (float): Epoch seconds.

  Returns:
    dict[tuple[str, str], dict[str, int]]: Fresh registry.

  Examples:
    >>> now = 8 * 86400
    >>> _fresh_consumed({("a", "b"): {"metrics": 0, "other": now - 1}}, now)
    {('a', 'b'): {'other': 691199}}
  """
  cutoff = now - CONSUMED_FRESH_DAYS * 86400
  out = {}
  for pair, stages in consumed.items():
    fresh = {s: t for s, t in stages.items() if t >= cutoff}
    if fresh:
      out[pair] = fresh
  return out


def _host_row(
  record: Mapping[str, Any], gap_sig: str | None, stale: bool
) -> dict[str, Any]:
  """
  Drilldown row for one host.

  Args:
    record (Mapping[str, Any]): Sweeper host record.
    gap_sig (str | None): The host's gap signature.
    stale (bool): Sweeper is stale (row is shown greyed).

  Returns:
    dict[str, Any]: Row fields for the UI.

  Examples:
    >>> _host_row({"fqdn": "a.x", "status": "current"}, "s", False)["fqdn"]
    'a.x'
  """
  return {
    "fqdn": record.get("fqdn"),
    "status": record.get("status") or ic.STATUS_UNKNOWN,
    "status_reason": record.get("status_reason"),
    "newest_sample": record.get("newest_sample"),
    "lag_vs_fleet_s": record.get("lag_vs_fleet_s"),
    "ingest_backlog_s": record.get("ingest_backlog_s"),
    "open_segment_age_s": record.get("open_segment_age_s"),
    "scanned_at": record.get("scanned_at"),
    "signature": gap_sig,
    "pair_count": record.get("pair_count"),
    "stale": bool(stale),
  }


def build_ingest_coverage_payload(
  *,
  fqdns: Sequence[str],
  host_records: Mapping[str, Mapping[str, Any]],
  sig_pairs: Mapping[str, Sequence[Sequence[str]]],
  consumed: Mapping[tuple[str, str], Mapping[str, int]],
  identities: Mapping[str, Mapping[str, Any]],
  beat: Mapping[str, Any] | None,
  enabled: bool,
  interval_s: int,
  window_minutes: int,
  now: float,
  group_by: str = "signature",
  errors: Sequence[str] = (),
) -> dict[str, Any]:
  """
  Classify, group and summarise coverage (pure; no I/O).

  Args:
    fqdns (Sequence[str]): ``recent_host`` inventory.
    host_records (Mapping[str, Mapping[str, Any]]): Sweeper records by FQDN.
    sig_pairs (Mapping[str, Sequence[Sequence[str]]]): Pair lists by
      signature.
    consumed (Mapping[tuple[str, str], Mapping[str, int]]): Raw consumed
      registry (``load_consumed``).
    identities (Mapping[str, Mapping[str, Any]]): ``monitor_identity`` by
      FQDN.
    beat (Mapping[str, Any] | None): Sweeper heartbeat.
    enabled (bool): Sweep switch.
    interval_s (int): Sweep interval (fallback for the heartbeat).
    window_minutes (int): Per-host window (display).
    now (float): Epoch seconds.
    group_by (str): Grouping mode.
    errors (Sequence[str]): Read errors to surface.

  Returns:
    dict[str, Any]: Section payload (see the plan's payload shape).

  Examples:
    >>> p = build_ingest_coverage_payload(
    ...   fqdns=[],
    ...   host_records={},
    ...   sig_pairs={},
    ...   consumed={},
    ...   identities={},
    ...   beat=None,
    ...   enabled=False,
    ...   interval_s=3600,
    ...   window_minutes=60,
    ...   now=0,
    ... )
    >>> p["sweeper_state"], p["groups"]
    ('sweep_disabled', [])
  """
  mode = normalize_group_by(group_by)
  state = sweeper_state(beat, enabled=enabled, interval_s=interval_s, now=now)
  stale = state == "sweeper_stale"
  fresh = _fresh_consumed(consumed, now)
  status_counts = dict.fromkeys(STATUS_KEYS, 0)
  scanned: dict[str, set[tuple[str, str]]] = {}
  excluded_behind: list[dict[str, Any]] = []
  out_errors = list(errors)
  for host in fqdns:
    record = host_records.get(host)
    if record is None:
      status_counts["not_scanned"] += 1
      continue
    status = record.get("status") or ic.STATUS_UNKNOWN
    status_counts[status if status in status_counts else ic.STATUS_UNKNOWN] += 1
    sig = record.get("sig")
    if status in (ic.STATUS_INCOMPLETE, ic.STATUS_NO_RECENT_DATA) or not sig:
      continue
    if sig not in sig_pairs:
      status_counts["not_scanned"] += 1
      out_errors.append(f"pair list missing for {host} (sig expired)")
      continue
    if status == ic.STATUS_INGEST_BEHIND:
      excluded_behind.append(_host_row(record, None, stale))
      continue
    scanned[host] = {(str(p[0]), str(p[1])) for p in sig_pairs[sig]}

  def _class(pair: tuple[str, str]) -> str:
    """
    Class of ``pair`` from the fresh registry.

    Args:
      pair (tuple[str, str]): ``(type, event)``.

    Returns:
      str: Pair class.

    Examples:
      >>> _class(("a", "b"))  # doctest: +SKIP
      'unused'
    """
    return classify_pair(fresh.get(pair, {}))

  registry_empty = not fresh
  gaps_by_host = {
    h: frozenset(p for p in pairs if _class(p) in GAP_CLASSES)
    for h, pairs in scanned.items()
  }
  gap_sig_by_host = {
    h: ic.pair_set_signature(gaps) for h, gaps in gaps_by_host.items()
  }

  def _group_key(host: str) -> str:
    """
    Grouping key of ``host`` for the active mode.

    Args:
      host (str): FQDN.

    Returns:
      str: Group key.

    Examples:
      >>> _group_key("c101-001.x")  # doctest: +SKIP
      'c101'
    """
    ident = identities.get(host)
    if mode == "rack":
      return rack_of(host)
    if mode == "schema":
      return schema_variant_of(ident)
    if mode == "slug":
      return str((ident or {}).get("capability_slug") or "unknown")
    return gap_sig_by_host[host]

  members: dict[str, list[str]] = defaultdict(list)
  for host in sorted(scanned):
    members[_group_key(host)].append(host)
  ordered = sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))
  groups: list[dict[str, Any]] = []
  if not registry_empty and state != "sweep_disabled":
    baseline_frac: dict[tuple[str, str], float] = {}
    baseline_sig = ""
    for idx, (key, hosts) in enumerate(ordered[:MAX_GROUPS]):
      group = _build_group(
        key,
        hosts,
        scanned=scanned,
        gaps_by_host=gaps_by_host,
        gap_sig_by_host=gap_sig_by_host,
        identities=identities,
        host_records=host_records,
        fresh=fresh,
        classify=_class,
        stale=stale,
      )
      fracs = {
        (p["type"], p["event"]): p["host_fraction"] for p in group["pairs"]
      }
      if idx == 0:
        baseline_frac = fracs
        baseline_sig = group["gap_signature"]
        group["is_baseline"] = True
      group["baseline_share"] = round(
        sum(gap_sig_by_host[h] == baseline_sig for h in hosts) / len(hosts), 4
      )
      if idx > 0:
        group["diff_vs_baseline"] = _diff_vs_baseline(
          group["pairs"], fracs, baseline_frac
        )
      groups.append(group)
  hosts_scanned = sum(
    1
    for h in fqdns
    if (host_records.get(h) or {}).get("status")
    not in (None, ic.STATUS_INCOMPLETE, ic.STATUS_NO_RECENT_DATA)
  )
  incomplete = (
    not fqdns
    or status_counts["not_scanned"] > 0
    or status_counts[ic.STATUS_INCOMPLETE] > 0
    or bool(out_errors)
  )
  if not fqdns:
    out_errors.append("No recent_host inventory in Valkey; scan incomplete.")
  beat = beat or {}
  payload = {
    "computed_at": int(now),
    "window_minutes": int(beat.get("window_minutes") or window_minutes),
    "consumed_fresh_days": CONSUMED_FRESH_DAYS,
    "group_by": mode,
    "group_by_modes": list(GROUP_BY_MODES),
    "sweeper_state": state,
    "sweep": {
      "started_at": beat.get("started_at"),
      "finished_at": beat.get("finished_at"),
      "hosts_done": beat.get("hosts_done"),
      "hosts_total": beat.get("hosts_total"),
      "interval_s": beat.get("interval_s") or interval_s,
      "last_error": beat.get("last_error"),
    },
    "last_sweep_at": beat.get("finished_at"),
    "hosts_total": len(fqdns),
    "hosts_scanned": hosts_scanned,
    "hosts_grouped": len(scanned),
    "incomplete": bool(incomplete),
    "errors": out_errors[:50],
    "status_counts": status_counts,
    "consumer_registry_empty": registry_empty,
    "consumed_pairs_fresh": len(fresh),
    "excluded_ingest_behind": excluded_behind[:MAX_HOSTS_PER_GROUP],
    "groups": groups,
    "groups_total": len(ordered),
  }
  payload["markdown"] = format_markdown(payload)
  return payload


def _build_group(
  key: str,
  hosts: Sequence[str],
  *,
  scanned: Mapping[str, set[tuple[str, str]]],
  gaps_by_host: Mapping[str, frozenset[tuple[str, str]]],
  gap_sig_by_host: Mapping[str, str],
  identities: Mapping[str, Mapping[str, Any]],
  host_records: Mapping[str, Mapping[str, Any]],
  fresh: Mapping[tuple[str, str], Mapping[str, int]],
  classify: Any,
  stale: bool,
) -> dict[str, Any]:
  """
  Summarise one group: mixes, per-pair classes and fractions, outliers.

  Args:
    key (str): Group key.
    hosts (Sequence[str]): Member FQDNs.
    scanned (Mapping[str, set[tuple[str, str]]]): Present pairs by host.
    gaps_by_host (Mapping[str, frozenset[tuple[str, str]]]): Gap sets.
    gap_sig_by_host (Mapping[str, str]): Gap signatures.
    identities (Mapping[str, Mapping[str, Any]]): Identities by host.
    host_records (Mapping[str, Mapping[str, Any]]): Sweeper records.
    fresh (Mapping[tuple[str, str], Mapping[str, int]]): Fresh registry.
    classify (Any): ``pair -> class`` callable.
    stale (bool): Sweeper stale flag for host rows.

  Returns:
    dict[str, Any]: Group document.

  Examples:
    >>> _build_group(
    ...   "k",
    ...   [],
    ...   scanned={},
    ...   gaps_by_host={},
    ...   gap_sig_by_host={},
    ...   identities={},
    ...   host_records={},
    ...   fresh={},
    ...   classify=lambda p: "unused",
    ...   stale=False,
    ... )["hosts"]
    0
  """
  n = len(hosts)
  present: Counter[tuple[str, str]] = Counter()
  all_zero: Counter[tuple[str, str]] = Counter()
  for host in hosts:
    present.update(scanned[host])
    for pair in (host_records.get(host) or {}).get("all_zero_pairs") or ():
      all_zero[(str(pair[0]), str(pair[1]))] += 1
  pairs = []
  for pair, count in present.items():
    cls = classify(pair)
    stages = fresh.get(pair, {})
    pairs.append(
      {
        "type": pair[0],
        "event": pair[1],
        "class": cls,
        "host_fraction": round(count / n, 4) if n else 0.0,
        "all_zero_fraction": round(all_zero[pair] / n, 4) if n else 0.0,
        "stages": sorted(stages),
        "last_consumed_at": max(stages.values()) if stages else None,
      }
    )
  order = {
    c: i for i, c in enumerate((*GAP_CLASSES, "type_detail_only", "derived"))
  }
  pairs.sort(key=lambda p: (order[p["class"]], p["type"], p["event"]))
  class_counts = Counter(p["class"] for p in pairs)
  majority_sig, _ = (
    Counter(gap_sig_by_host[h] for h in hosts).most_common(1)[0]
    if hosts
    else ("", 0)
  )
  majority_host = next(
    (h for h in hosts if gap_sig_by_host[h] == majority_sig), None
  )
  majority_gaps = (
    gaps_by_host.get(majority_host, frozenset())
    if majority_host
    else frozenset()
  )
  outliers = []
  for host in hosts:
    if gap_sig_by_host[host] == majority_sig:
      continue
    gaps = gaps_by_host[host]
    outliers.append(
      {
        "fqdn": host,
        "extra_gaps": [list(p) for p in sorted(gaps - majority_gaps)][:50],
        "missing_vs_group": [
          list(p)
          for p in sorted(
            set().union(*(scanned[h] for h in hosts)) - scanned[host]
          )
        ][:50],
      }
    )
  sent_types = {t for t, _e in present}
  consumed_not_ingested = sorted(
    [t, e]
    for (t, e), stages in fresh.items()
    if t in sent_types
    and (t, e) not in present
    and set(stages) - {STAGE_TYPE_DETAIL_DROPPED}
  )
  idents = [identities.get(h) or {} for h in hosts]
  return {
    "key": key,
    "hosts": n,
    "gap_signature": majority_sig,
    "baseline_share": 0.0,
    "is_baseline": False,
    "slugs": dict(
      Counter(str(i.get("capability_slug") or "unknown") for i in idents)
    ),
    "host_prefixes": dict(Counter(rack_of(h) for h in hosts)),
    "schema_variants": dict(Counter(schema_variant_of(i) for i in idents)),
    "class_counts": {c: class_counts.get(c, 0) for c in order},
    "gap_count": sum(class_counts.get(c, 0) for c in GAP_CLASSES),
    "pairs": pairs[:MAX_PAIRS_PER_GROUP],
    "pairs_total": len(pairs),
    "diff_vs_baseline": [],
    "outlier_hosts": outliers[:MAX_OUTLIERS_PER_GROUP],
    "host_rows": [
      _host_row(host_records.get(h) or {"fqdn": h}, gap_sig_by_host[h], stale)
      for h in hosts[:MAX_HOSTS_PER_GROUP]
    ],
    "consumed_not_ingested": consumed_not_ingested[:MAX_CONSUMED_NOT_INGESTED],
  }


def _diff_vs_baseline(
  pairs: Iterable[Mapping[str, Any]],
  fracs: Mapping[tuple[str, str], float],
  baseline: Mapping[tuple[str, str], float],
) -> list[dict[str, Any]]:
  """
  Pairs whose host fraction differs from the baseline (largest) group.

  Args:
    pairs (Iterable[Mapping[str, Any]]): This group's pair rows.
    fracs (Mapping[tuple[str, str], float]): This group's fractions.
    baseline (Mapping[tuple[str, str], float]): Baseline fractions.

  Returns:
    list[dict[str, Any]]: ``type``, ``event``, ``class``,
    ``host_fraction``, ``baseline_fraction`` (capped).

  Examples:
    >>> _diff_vs_baseline(
    ...   [
    ...     {"type": "a", "event": "b", "class": "unused", "host_fraction": 1.0}
    ...   ],
    ...   {("a", "b"): 1.0},
    ...   {},
    ... )[0]["baseline_fraction"]
    0.0
  """
  cls_by_pair = {(p["type"], p["event"]): p["class"] for p in pairs}
  out = []
  for pair in sorted(set(fracs) | set(baseline)):
    here, there = fracs.get(pair, 0.0), baseline.get(pair, 0.0)
    if here != there:
      out.append(
        {
          "type": pair[0],
          "event": pair[1],
          "class": cls_by_pair.get(pair, "not_sent"),
          "host_fraction": here,
          "baseline_fraction": there,
        }
      )
  return out[:MAX_PAIRS_PER_GROUP]


def format_markdown(payload: Mapping[str, Any]) -> str:
  """
  Copy-as-markdown summary of the payload (gap pairs per group).

  Args:
    payload (Mapping[str, Any]): Section payload.

  Returns:
    str: Markdown text.

  Examples:
    >>> "Ingest vs Job Detail" in format_markdown(
    ...   {"groups": [], "sweeper_state": "ok", "status_counts": {}}
    ... )
    True
  """
  lines = [
    "## Ingest vs Job Detail coverage",
    "",
    f"- sweeper: {payload.get('sweeper_state')}; group by "
    f"{payload.get('group_by')}; window {payload.get('window_minutes')} min",
    f"- hosts: {payload.get('hosts_grouped', 0)} grouped of "
    f"{payload.get('hosts_total', 0)}; status "
    + ", ".join(
      f"{k}={v}" for k, v in (payload.get("status_counts") or {}).items()
    ),
  ]
  if payload.get("consumer_registry_empty"):
    lines.append("- update_metrics has not recorded reads yet; no gaps listed")
  if payload.get("incomplete"):
    lines.append("- scan incomplete: results are not a healthy signal")
  for group in payload.get("groups") or []:
    lines += [
      "",
      f"### {group['key']} ({group['hosts']} hosts, {group['gap_count']} gaps)",
    ]
    for pair in group["pairs"]:
      if pair["class"] in GAP_CLASSES:
        lines.append(
          f"- `{pair['type']}` / `{pair['event']}`: {pair['class']} "
          f"({pair['host_fraction']:.0%} of hosts)"
        )
  return "\n".join(lines) + "\n"


def compute_ingest_coverage(
  *, force_refresh: bool = False, group_by: Any = None
) -> dict[str, Any]:
  """
  Build (and cache for 60 s) the section payload from Valkey only.

  Soft-fails to an ``incomplete`` payload when Valkey is unavailable or a
  read fails; never raises to the view.

  Args:
    force_refresh (bool): Skip the cached entry (``refresh=1``).
    group_by (Any): Group-by query value.

  Returns:
    dict[str, Any]: Section payload.

  Examples:
    >>> compute_ingest_coverage(force_refresh=True)  # doctest: +SKIP
    {'sweeper_state': 'ok', ...}
  """
  mode = normalize_group_by(group_by)
  key = cache_key_for(mode)
  if not force_refresh:
    with contextlib.suppress(Exception):
      cached = cache.get(key)
      if isinstance(cached, dict):
        return cached
  now = time.time()
  enabled = cfg.get_ingest_coverage_sweep_enabled()
  interval_s = cfg.get_ingest_coverage_sweep_interval_s()
  window = cfg.get_ingest_coverage_window_minutes()
  errors: list[str] = []
  fqdns: list[str] = []
  records: dict[str, dict[str, Any]] = {}
  sigs: dict[str, list[list[str]]] = {}
  consumed: dict[tuple[str, str], dict[str, int]] = {}
  identities: dict[str, dict[str, Any]] = {}
  beat = None
  client = get_valkey_client()
  if client is None:
    errors.append("Valkey unavailable; scan results cannot be read.")
  else:
    try:
      fqdns = sorted(set(list_recent_host_fqdns_from_valkey()))
      beat = ic.load_json(client, ic.SWEEP_KEY)
      by_key = _mget_json(client, [ic.host_key(h) for h in fqdns])
      records = {
        h: by_key[ic.host_key(h)] for h in fqdns if ic.host_key(h) in by_key
      }
      wanted = sorted({r["sig"] for r in records.values() if r.get("sig")})
      sig_docs = _mget_json(client, [ic.sig_key(s) for s in wanted])
      sigs = {
        s: sig_docs[ic.sig_key(s)].get("pairs") or []
        for s in wanted
        if ic.sig_key(s) in sig_docs
      }
      consumed = load_consumed(client)
      ident_docs = _mget_json(
        client, [monitor_identity_valkey_key(h) for h in fqdns]
      )
      identities = {
        h: ident_docs[monitor_identity_valkey_key(h)]
        for h in fqdns
        if monitor_identity_valkey_key(h) in ident_docs
      }
    except Exception as exc:
      logger.warning("admin_monitor ingest_coverage read failed: %s", exc)
      errors.append(f"Valkey read failed: {type(exc).__name__}")
  payload = build_ingest_coverage_payload(
    fqdns=fqdns,
    host_records=records,
    sig_pairs=sigs,
    consumed=consumed,
    identities=identities,
    beat=beat,
    enabled=enabled,
    interval_s=interval_s,
    window_minutes=window,
    now=now,
    group_by=mode,
    errors=errors,
  )
  with contextlib.suppress(Exception):
    cache.set(key, payload, timeout=TIMEOUT_ADMIN_INGEST_COVERAGE)
  return payload
