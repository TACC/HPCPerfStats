"""Production Codon tree: columnar bake units only; file IPC is not legacy-gated."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CODON = _REPO / "hpcperfstats" / "dbload" / "codon"


def test_codon_bake_tree_has_no_retired_maximize_surface_units() -> None:
  """Extra inventory kernels are not part of the production columnar compile graph."""
  for name in ("collapse_sum_kernel.py", "feed_collapse_bench.py"):
    assert not (_CODON / name).is_file(), name


def test_worker_stdio_loop_serves_parse_file_without_legacy_env() -> None:
  """Columnar file sessions must not sit behind KIND_PARSE legacy gating."""
  src = (
    _REPO / "hpcperfstats" / "dbload" / "lib" / "codon_process_worker.py"
  ).read_text(encoding="utf-8")
  main_body = src.split("def main() -> int:", 1)[1].split(
    'if __name__ == "__main__":', 1
  )[0]
  fp_pos = main_body.index('kind == "fp"')
  file_pos = main_body.index('kind == "parse_file"')
  legacy_pos = main_body.index("HPCPERFSTATS_CODON_IPC_LEGACY_PARSE")
  assert fp_pos < file_pos < legacy_pos, (fp_pos, file_pos, legacy_pos)


def test_codon_readme_lists_columnar_units_only() -> None:
  readme = (_CODON / "README.md").read_text(encoding="utf-8")
  assert "collapse_sum_kernel" not in readme
  assert "feed_collapse_bench" not in readme
  assert "feed_collapse_capi.py" in readme
  assert "feed_collapse.py" in readme
