"""Gate: every host_data reader treats monitor value sentinels as missing.

``host_data.value < 0`` is a monitor value sentinel (``monitor_sentinel``),
never data. Each production module that queries ``host_data`` is classified
here; a new reader fails this test until it is classified, and each class
has a check that the module really applies the rule.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_QUERY = re.compile(r"FROM host_data\b|JOIN host_data\b|host_data\.objects")

# Values reach analysis only through ``build_job_frame``, which takes the
# sentinel codes out of ``value`` (``JobFrame.sentinel``).
FRAME = "frame"
# Raw SQL that reads ``value``: must carry the ``value >= 0`` fragment.
SQL_VALUE = "sql_value"
# Reads time / host / schema / presence only, or writes rows.
NO_VALUE = "no_value"
# Recomputes deltas from stored values; sentinel rows are skipped explicitly.
REPAIR = "repair"
# Operates on legacy per-core rows, which predate value sentinels.
LEGACY = "legacy"

READERS: dict[str, tuple[str, str]] = {
  "analysis/metrics/lib/columnar/copy_fetch.py": (
    FRAME,
    "COPY -> build_job_frame",
  ),
  "analysis/metrics/lib/metrics.py": (NO_VALUE, "Min/Max(time) window bounds"),
  "analysis/metrics/lib/gen/jid_table.py": (
    NO_VALUE,
    "window filter dicts and row-count estimates; values via the frame",
  ),
  "analysis/metrics/lib/metrics_host_data_sql.py": (
    NO_VALUE,
    "time probes, distinct type/event/unit, distinct-time counts",
  ),
  "dbload/lib/ingest_coverage.py": (SQL_VALUE, "coverage scan has_nonzero"),
  "dbload/lib/monitor_mark_side_ingest.py": (
    NO_VALUE,
    "max sample time per jid",
  ),
  "dbload/lib/sync_timedb_host_data_insert.py": (NO_VALUE, "writer"),
  "dbload/lib/sync_timedb_host_itimes.py": (NO_VALUE, "ingested sample times"),
  "dbload/lib/sync_timedb_ingest_readiness.py": (
    NO_VALUE,
    "head/tail presence",
  ),
  "site/lib/machine/admin_monitor_telemetry_health.py": (
    SQL_VALUE,
    "telemetry has_nonzero + per-code sentinel counts",
  ),
  "site/lib/machine/api.py": (NO_VALUE, "newest sample time"),
  "site/lib/machine/host_data_latest.py": (
    NO_VALUE,
    "latest sample time per host",
  ),
  "site/lib/machine/management/commands/repair_jstamp_deltas.py": (
    REPAIR,
    "host_data_jstamp_delta_repair.plan_series skips sentinel rows",
  ),
  "site/lib/machine/management/commands/collapse_percore_host_data.py": (
    LEGACY,
    "legacy per-core rows; ingest collapses devices at parse time now",
  ),
}


def find_host_data_readers(root: Path) -> set[str]:
  """Relative paths of non-test modules under ``root`` that query host_data."""
  found: set[str] = set()
  for path in root.rglob("*.py"):
    rel = path.relative_to(root).as_posix()
    if (
      "/tests/" in f"/{rel}"
      or "/migrations/" in rel
      or rel.endswith("conftest.py")
    ):
      continue
    if _QUERY.search(path.read_text(errors="replace")):
      found.add(rel)
  return found


def test_every_host_data_reader_is_classified():
  unclassified = find_host_data_readers(ROOT) - set(READERS)
  assert not unclassified, (
    "host_data readers must be classified for value sentinels "
    f"(value < 0 is never data): {sorted(unclassified)}"
  )


def test_classified_readers_still_exist():
  stale = [rel for rel in READERS if not (ROOT / rel).is_file()]
  assert not stale, f"remove stale entries: {stale}"


def test_sql_value_readers_apply_value_ge_zero():
  for rel, (kind, _why) in READERS.items():
    if kind == SQL_VALUE:
      assert "value >= 0" in (ROOT / rel).read_text(), rel


def test_frame_path_takes_sentinels_out_of_value():
  jobframe = (ROOT / "analysis/metrics/lib/columnar/jobframe.py").read_text()
  assert "codes = sentinel_codes(value)" in jobframe
  assert "np.where(codes != 0, np.nan, value)" in jobframe
  for rel, (kind, _why) in READERS.items():
    if kind == FRAME:
      assert "build_job_frame(" in (ROOT / rel).read_text(), rel


def test_repair_reader_skips_sentinel_rows():
  repair = (ROOT / "dbload/lib/host_data_jstamp_delta_repair.py").read_text()
  assert "value < 0" in repair


def test_gate_flags_a_planted_unclassified_reader(tmp_path):
  """Positive control: the discovery finds a new reader it does not know."""
  pkg = tmp_path / "site" / "lib"
  pkg.mkdir(parents=True)
  (pkg / "new_reader.py").write_text(
    'SQL = "SELECT value FROM host_data WHERE host = %s"\n'
  )
  (tmp_path / "tests").mkdir()
  (tmp_path / "tests" / "test_x.py").write_text('Q = "FROM host_data"\n')
  assert find_host_data_readers(tmp_path) == {"site/lib/new_reader.py"}
