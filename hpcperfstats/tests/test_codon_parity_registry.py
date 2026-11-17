"""Registry of Codon / CPython twin implementations and the tests that compare them.

Rule: ``codon-python-parity-contract.mdc``. Codon is canonical on the 3.14t
pipeline; the CPython / NumPy twin is the reference oracle. Every behaviour has
a row here naming both sides and at least one test that runs both on the same
input. The guards below fail when a Codon export, a NumPy reference, or a new
CPython-parser call site appears without a row, or when a row points at a
symbol or test that no longer exists.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "hpcperfstats"
DBLOAD_UNITS = (
  PKG / "dbload" / "codon" / "feed_collapse.py",
  PKG / "dbload" / "codon" / "feed_collapse_capi.py",
)
METRICS_UNIT = PKG / "analysis" / "metrics" / "codon" / "metrics_kernels.py"
SEGMENTS = PKG / "analysis" / "metrics" / "lib" / "columnar" / "segments.py"
SENTINEL_PROFILE = PKG / "analysis" / "metrics" / "lib" / "sentinel_profile.py"


@dataclass(frozen=True)
class ParityFeature:
  """One behaviour implemented twice.

  ``cpython`` is ``module:qualname``; ``codon`` names functions defined in a
  Codon compile unit; ``tests`` are ``path::function`` nodes (repo-relative)
  that run both sides on the same input.
  """

  name: str
  cpython: str
  codon: tuple[str, ...]
  tests: tuple[str, ...]


_P = "hpcperfstats.dbload.lib.sync_timedb_parsing"
_T = "hpcperfstats/tests"
_MT = "hpcperfstats/analysis/metrics/tests/test_codon_kernels.py"
_SEG = "hpcperfstats.analysis.metrics.lib.columnar.segments"
_SP = "hpcperfstats.analysis.metrics.lib.sentinel_profile"

PARITY_FEATURES: tuple[ParityFeature, ...] = (
  ParityFeature(
    "stats line feed (schema, @fast/@full, value count, whitespace)",
    f"{_P}:IncrementalStatsParser.feed_line",
    ("feed_lines_full", "_append_stats"),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_feed_delta_collapse_arc_matches_cpython_broad_corpus",
      f"{_T}/test_codon_canonical_parity.py::test_leading_whitespace_and_non_alpha_types_match_cpython",
    ),
  ),
  ParityFeature(
    "counter deltas (wrap, mult, carry)",
    f"{_P}:_apply_counter_deltas",
    ("apply_counter_deltas_cols",),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_apply_counter_deltas_cols_wrap_and_carry_continuity",
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_counter_drop_across_restart_breaks_chain_both_engines",
    ),
  ),
  ParityFeature(
    "$ header capture ($build bridge flag, restart times, window cut)",
    f"{_P}:IncrementalStatsParser._record_header_sample",
    ("scan_monitor_headers", "_header_bridge_flag"),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_sentinel_corpora_codon_matches_cpython",
    ),
  ),
  ParityFeature(
    "contents path: $ header state reaches the delta step",
    f"{_P}:parse_stats_lines",
    ("scan_monitor_headers", "feed_delta_collapse_arc"),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_whole_file_cpython_path_matches_codon",
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_whole_file_cpython_path_breaks_chains_at_restart_headers",
      f"{_T}/test_codon_canonical_parity.py::test_pyext_matches_cpython_whole_file_path",
      f"{_T}/test_sync_timedb.py::test_payload_without_codon_breaks_chain_at_restart_header",
    ),
  ),
  ParityFeature(
    "value sentinel deltas (bridge, chain break, limit, override, carry)",
    f"{_P}:_sentinel_deltas",
    ("apply_counter_deltas_cols", "_sentinel_breaks", "_restart_in_gap"),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_sentinel_corpora_codon_matches_cpython",
    ),
  ),
  ParityFeature(
    "value sentinel collapse (valid-only, modal code, per-device bridged arc)",
    f"{_P}:_compute_deltas_and_arc_impl",
    (
      "sentinel_collapse_prep",
      "sentinel_restore_cols",
      "sentinel_bridge_arc_cols",
      "_collapse_dev_of",
    ),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_sentinel_corpora_codon_matches_cpython",
    ),
  ),
  ParityFeature(
    "collapse (host sum, keep-dev, j: stamps, nvidia kinds, CCM)",
    f"{_P}:_collapse_stats_with_deltas",
    ("collapse_full_cols",),
    (
      f"{_T}/test_codon_collapse_parity.py::test_plain_host_collapse_matches_groupby_sum",
      f"{_T}/test_keep_dev_type_set_parity.py::test_codon_collapse_matches_cpython_for_socket_rows",
      f"{_T}/test_codon_shared_stamp_per_device_deltas.py::test_shared_stamp_keeps_per_device_deltas",
    ),
  ),
  ParityFeature(
    "arc rates (carry, dropna)",
    f"{_P}:_apply_arc_and_finalize",
    ("apply_arc_cols",),
    (
      f"{_T}/test_codon_feed_delta_collapse_arc.py::test_apply_arc_cols_carry_continuity_and_dropna",
    ),
  ),
  ParityFeature(
    "keep-dev / GPU literal type sets",
    "hpcperfstats.dbload.lib.monitor_naming.canonical:KEEP_DEV_COLLAPSE_TYPES",
    ("_is_keep_dev_type",),
    (
      f"{_T}/test_keep_dev_type_set_parity.py::test_codon_literal_mirrors_match_canonical_sets",
    ),
  ),
  ParityFeature(
    "legacy CTL/CTR decode",
    "hpcperfstats.dbload.lib.sync_timedb_parsing_legacy:map_hardware_counter_vals",
    ("legacy_decode_tokens", "schema_needs_legacy_decode"),
    (
      f"{_T}/test_codon_canonical_parity.py::test_legacy_ctl_ctr_decode_matches_cpython",
      f"{_T}/test_codon_canonical_parity.py::test_legacy_literal_tables_match_cpython",
      f"{_T}/test_codon_canonical_parity.py::test_legacy_fixed_ctr_without_mapping_fails_like_cpython",
    ),
  ),
  ParityFeature(
    "%jobs / %affinity marks and shared-sample arm",
    f"{_P}:IncrementalStatsParser._note_percent_mark",
    ("feed_lines_full", "_parse_jobs_jids", "_parse_affinity_mark"),
    (
      f"{_T}/test_codon_marks_parity.py::test_codon_marks_match_incremental_ready",
    ),
  ),
  ParityFeature(
    "resume emission gate (start_idx)",
    f"{_P}:IncrementalStatsParser.feed_line",
    ("feed_lines_full", "_fs_take_line"),
    (
      f"{_T}/test_codon_canonical_parity.py::test_resume_emission_gate_rows_match_cpython",
      f"{_T}/test_codon_canonical_parity.py::test_resume_emission_gate_marks_and_stamps_match_cpython",
      f"{_T}/test_codon_canonical_parity.py::test_pyext_file_session_gate_and_window_arm_match_source",
    ),
  ),
  ParityFeature(
    "file-session windows (one parser in CPython; carried arm in Codon)",
    "hpcperfstats.dbload.lib.codon_file_bundles:iter_codon_flush_line_bundles",
    ("_fs_cut_window", "trailing_jobs_arm"),
    (
      f"{_T}/test_codon_canonical_parity.py::test_window_split_keeps_shared_stamp_across_boundary",
      f"{_T}/test_codon_canonical_parity.py::test_pyext_file_session_gate_and_window_arm_match_source",
    ),
  ),
  ParityFeature(
    "proc / host_proc rows (alias, key set, null last-write, peaks)",
    f"{_P}:IncrementalStatsParser._merge_proc_row_online",
    ("_merge_proc",),
    (f"{_T}/test_codon_canonical_parity.py::test_proc_rows_match_cpython",),
  ),
  ParityFeature(
    "dynamic exclude types",
    f"{_P}:IncrementalStatsParser.feed_line",
    ("feed_lines_full",),
    (
      f"{_T}/test_codon_exclude_parity.py::test_kernel_exclude_matches_cpython_parser",
    ),
  ),
  ParityFeature(
    "listend live sample ingest",
    "hpcperfstats.dbload.lib.sync_timedb_sample_ingest:ingest_monitor_sample_text",
    ("feed_delta_collapse_arc",),
    (
      f"{_T}/test_listend_codon_divert.py::test_listend_codon_divert_matches_cpython",
    ),
  ),
  ParityFeature(
    "metrics group reduce (row-order sum, max, NaN)",
    f"{_SEG}:_np_group_reduce",
    ("group_reduce_c",),
    (
      f"{_MT}::test_group_reduce_matches_numpy_reference",
      f"{_MT}::test_group_reduce_sums_in_row_order_bit_exact",
      f"{_MT}::test_group_reduce_negative_zero_and_all_nan_groups",
    ),
  ),
  ParityFeature(
    "metrics residual collapse (hardware sum else j: max)",
    f"{_SEG}:_np_residual_collapse",
    ("residual_collapse_c",),
    (f"{_MT}::test_residual_collapse_matches_numpy_reference_bit_exact",),
  ),
  ParityFeature(
    "metrics grid mean",
    f"{_SEG}:_np_grid_mean",
    ("grid_mean_c",),
    (f"{_MT}::test_grid_mean_and_join_match_numpy_reference",),
  ),
  ParityFeature(
    "metrics sorted join",
    f"{_SEG}:_np_join_keys",
    ("join_sorted_c",),
    (f"{_MT}::test_grid_mean_and_join_match_numpy_reference",),
  ),
  ParityFeature(
    "metrics COPY BINARY decode",
    "hpcperfstats.analysis.metrics.lib.columnar.k0_decode:decode_copy_binary_reference",
    ("copy_count_rows_c", "copy_decode_c"),
    (f"{_MT}::test_copy_decode_matches_reference",),
  ),
  ParityFeature(
    "metrics FrameIndex reductions end to end",
    "hpcperfstats.analysis.metrics.lib.columnar.kernels:FrameIndex",
    ("group_reduce_c", "residual_collapse_c"),
    (f"{_MT}::test_frame_index_reductions_codon_equal_numpy_bit_exact",),
  ),
  ParityFeature(
    "metrics sentinel sample states (row-order sums, last code, first row)",
    f"{_SP}:_np_sentinel_samples",
    ("sentinel_samples_c",),
    (
      f"{_MT}::test_sentinel_samples_matches_numpy_twin_randomized",
      f"{_MT}::test_sentinel_samples_edge_cases_match_numpy_twin",
      f"{_MT}::test_sentinel_profile_end_to_end_codon_equals_numpy",
    ),
  ),
  ParityFeature(
    "metrics sentinel bucket fold (covered means, gap flags, first drop)",
    f"{_SP}:_np_sentinel_fold",
    ("sentinel_fold_c",),
    (
      f"{_MT}::test_sentinel_fold_matches_numpy_twin_randomized",
      f"{_MT}::test_sentinel_fold_edge_cases_match_numpy_twin",
      f"{_MT}::test_sentinel_profile_end_to_end_codon_equals_numpy",
    ),
  ),
  ParityFeature(
    "metrics sentinel interior gap fill (max_gap, max_frac, anchors)",
    f"{_SP}:_np_fill_interior_gaps",
    ("fill_interior_gaps_c",),
    (
      f"{_MT}::test_sentinel_fill_interior_gaps_matches_numpy_twin",
      f"{_MT}::test_sentinel_fill_interior_gaps_boundaries_match_numpy_twin",
    ),
  ),
  ParityFeature(
    "metrics per-sample sums under sentinel sample states (gap, bridge, fill)",
    f"{_SP}:sentinel_sample_mask",
    ("sentinel_samples_c", "fill_interior_gaps_c"),
    (
      f"{_MT}::test_sentinel_series_sums_codon_equals_numpy",
      f"{_MT}::test_sentinel_profile_end_to_end_codon_equals_numpy",
    ),
  ),
)

# Exports that move data across the C ABI (slot banks, carry, errors, setters,
# the parse entry points behind the rows above); the bake smoke covers them.
_ABI_EXPORT = re.compile(
  r"^(psm_|pss_|psp_|codon_carry_|codon_last_error_c$|codon_host_parsed_n_c$"
  r"|codon_exclude_set_c$|codon_start_idx_set_c$|feed_and_collapse_fingerprint_c$"
  r"|parse_stats_lines_codon_run_c$|parse_stats_file_codon_(open|next)_c$)"
)

# Production modules allowed to call the CPython parse path directly: the
# boot-disabled (no baked .so) branch of a Codon-first caller, or a listed
# exception in codon-python-parity-contract.mdc.
CPYTHON_PARSE_CALLERS = {
  "hpcperfstats/dbload/lib/sync_timedb_parsing.py",
  "hpcperfstats/dbload/lib/sync_timedb_sample_ingest.py",
  "hpcperfstats/dbload/sync_timedb.py",
  "hpcperfstats/dbload/lib/host_data_percore_collapse.py",
}
_CPYTHON_PARSE_CALL = re.compile(
  r"\b(IncrementalStatsParser|compute_deltas_and_arc_chunk|compute_deltas_and_arc"
  r"|_collapse_stats_with_deltas)\("
)


def _defined_functions(path: Path) -> set[str]:
  return set(re.findall(r"^def (\w+)\(", path.read_text(), re.M))


def _exports(path: Path) -> list[str]:
  return re.findall(r"^@export\ndef (\w+)\(", path.read_text(), re.M)


def _codon_defs() -> set[str]:
  out: set[str] = set()
  for unit in (*DBLOAD_UNITS, METRICS_UNIT):
    out |= _defined_functions(unit)
  return out


def _resolve(ref: str) -> object:
  mod_name, qual = ref.split(":")
  obj: object = importlib.import_module(mod_name)
  for part in qual.split("."):
    obj = getattr(obj, part)
  return obj


@pytest.mark.parametrize("feature", PARITY_FEATURES, ids=lambda f: f.name)
def test_registry_row_resolves(feature: ParityFeature) -> None:
  _resolve(feature.cpython)
  missing = set(feature.codon) - _codon_defs()
  assert not missing, f"{feature.name}: Codon functions not defined: {missing}"
  assert feature.tests, f"{feature.name}: no parity test"
  for node in feature.tests:
    path, func = node.split("::")
    tree = ast.parse((ROOT / path).read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert func in names, f"{feature.name}: missing test {node}"


def test_every_codon_export_is_registered_or_abi() -> None:
  registered = {c for f in PARITY_FEATURES for c in f.codon}
  exports = [
    e for unit in (*DBLOAD_UNITS, METRICS_UNIT) for e in _exports(unit)
  ]
  unregistered = [
    e for e in exports if e not in registered and not _ABI_EXPORT.match(e)
  ]
  assert not unregistered, (
    "Codon exports without a PARITY_FEATURES row (add the CPython twin and a "
    f"parity test): {unregistered}"
  )


def test_every_numpy_reference_has_a_codon_twin() -> None:
  refs = {f.cpython.split(":")[1] for f in PARITY_FEATURES}
  np_refs = {
    n
    for path in (SEGMENTS, SENTINEL_PROFILE)
    for n in _defined_functions(path)
    if n.startswith("_np_")
  }
  assert np_refs <= refs, f"NumPy references without a row: {np_refs - refs}"


def test_cpython_parse_callers_are_allowlisted() -> None:
  found = set()
  for path in PKG.rglob("*.py"):
    rel = path.relative_to(ROOT).as_posix()
    if "/tests/" in rel or "/codon/" in rel:
      continue
    for line in path.read_text().splitlines():
      code = line.split("#", 1)[0]
      if _CPYTHON_PARSE_CALL.search(code) and not code.lstrip().startswith(
        (">>>", "def ")
      ):
        found.add(rel)
  assert found <= CPYTHON_PARSE_CALLERS, (
    "New CPython parse call sites must go Codon-first (or be added as an "
    f"exception in codon-python-parity-contract.mdc): {found - CPYTHON_PARSE_CALLERS}"
  )


def test_tests_load_the_baked_pyext_only_through_the_conftest_fixture() -> None:
  """A direct import skips the libomp deep-bind and hangs pytest at exit."""
  direct = [
    p.relative_to(ROOT).as_posix()
    for p in (PKG / "tests").glob("*.py")
    if p.name != "conftest.py"
    and re.search(r"^\s*import hpcperfstats_codon_parse\b", p.read_text(), re.M)
  ]
  assert not direct, f"use the conftest ``pyext`` fixture instead: {direct}"
