"""Staff Job Detail diagnostics for plot/detail artifact schema versions."""
from __future__ import annotations

from typing import Any, Dict, List

from hpcperfstats.site.lib.machine import job_detail_artifacts as detail_cfg
from hpcperfstats.site.lib.machine import job_plot_artifacts as plot_cfg
from hpcperfstats.site.lib.machine.models import job_detail_artifact, job_plot_artifact


def _distinct_non_null_schemas(values) -> List[int]:
  out: List[int] = []
  seen = set()
  for raw in values:
    if raw is None:
      continue
    try:
      n = int(raw)
    except (TypeError, ValueError):
      continue
    if n not in seen:
      seen.add(n)
      out.append(n)
  out.sort()
  return out


def staff_artifact_contract_payload(jid: str) -> Dict[str, Any]:
  """Runtime APP_* schema ints plus distinct stored schemas for this job.

  ``db_plot`` / ``db_detail`` omit null (legacy) rows; empty list means no
  readable stored schema for that family.
  """
  plot_vals = job_plot_artifact.objects.filter(jid_id=jid).values_list(
      "artifact_schema", flat=True
  ).distinct()
  detail_vals = job_detail_artifact.objects.filter(jid_id=jid).values_list(
      "artifact_schema", flat=True
  ).distinct()
  return {
      "current_plot": plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION,
      "current_detail": detail_cfg.APP_DETAIL_ARTIFACT_SCHEMA_VERSION,
      "db_plot": _distinct_non_null_schemas(plot_vals),
      "db_detail": _distinct_non_null_schemas(detail_vals),
  }
