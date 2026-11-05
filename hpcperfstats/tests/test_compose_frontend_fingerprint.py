"""Regression: SPA deploy fingerprints must never be empty.

The shell helpers used to grep ``machine/index.html`` for ``page-<hex>.js``.
Turbopack chunk names (``_next/static/chunks/0-o74znen4827.js``) never match,
so ``rebuild_pipeline.sh`` compared ``""`` to ``""`` and its frontend drift
restore silently never fired (hpcperfstats-dev, 2026-11-04). All three
helpers now return the sha256 of ``machine/index.html`` -- the same value as
``spa_static_root_heal.spa_shell_fingerprint`` -- or ``unknown``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hpcperfstats.site.lib.spa_static_root_heal import spa_shell_fingerprint

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HELPERS = _REPO_ROOT / "scripts" / "lib" / "compose_frontend_helpers.sh"
# Real chunk naming from the 2026-11 Next export (no ``page-`` prefix).
_SHELL_HTML = (
  '<!DOCTYPE html><html><head><script src="/static/frontend/_next/static/'
  'chunks/0-o74znen4827.js" async></script></head><body></body></html>\n'
)


def _run_helper(snippet: str, tmp_path: Path) -> str:
  """Source the helpers with a fake compose that runs ``exec`` locally.

  Args:
    snippet (str): Bash to run after sourcing (may call helper functions).
    tmp_path (Path): Scratch dir used as TMPDIR.

  Returns:
    str: Stripped stdout of the snippet.

  Examples:
    >>> _run_helper("echo ok", Path("/tmp"))  # doctest: +SKIP
    'ok'
  """
  script = f"""
set -euo pipefail
export TMPDIR={tmp_path}
source {_HELPERS}
fake_compose() {{ shift 3; "$@"; }}  # drop: exec -T <service>
PODMAN_COMPOSE=(fake_compose)
{snippet}
"""
  out = subprocess.run(
    ["bash", "-c", script], capture_output=True, text=True, check=True
  )
  return out.stdout.strip()


@pytest.fixture
def frontend(tmp_path: Path) -> Path:
  """Create a minimal frontend tree with a Turbopack-style machine shell."""
  root = tmp_path / "frontend"
  (root / "machine").mkdir(parents=True)
  (root / "machine" / "index.html").write_text(_SHELL_HTML)
  return root


def test_host_fingerprint_is_sha256_matching_python_heal(
  frontend: Path, tmp_path: Path
) -> None:
  """Host fingerprint equals the Python heal fingerprint, never empty."""
  index = frontend / "machine" / "index.html"
  got = _run_helper(f"frontend_build_fingerprint '{index}'", tmp_path)
  assert got == spa_shell_fingerprint(frontend)
  assert len(got) == 64


def test_container_fingerprint_matches_host(
  frontend: Path, tmp_path: Path
) -> None:
  """In-container fingerprint uses the same sha256 as the host side."""
  index = frontend / "machine" / "index.html"
  got = _run_helper(f"fingerprint_in_container web '{index}'", tmp_path)
  assert got == spa_shell_fingerprint(frontend)


def test_missing_shell_reports_unknown(tmp_path: Path) -> None:
  """A missing shell is ``unknown`` on both sides, not an empty string."""
  missing = tmp_path / "nope" / "machine" / "index.html"
  host = _run_helper(f"frontend_build_fingerprint '{missing}'", tmp_path)
  ctr = _run_helper(f"fingerprint_in_container web '{missing}'", tmp_path)
  assert host == ctr == "unknown"


def test_extract_verify_passes_for_identical_trees(
  frontend: Path, tmp_path: Path
) -> None:
  """Extract verification compares like with like and passes on a copy."""
  index = frontend / "machine" / "index.html"
  got = _run_helper(
    f"verify_container_extract_fingerprint '{frontend}' lbl '{index}'",
    tmp_path,
  )
  assert got.startswith("Verified extract fingerprint for lbl (")


def test_no_chunk_name_greps_remain() -> None:
  """Chunk-name greps break on every Next naming change; keep them out."""
  for path in (
    _HELPERS,
    _REPO_ROOT / "scripts" / "rebuild_pipeline.sh",
    _REPO_ROOT / "scripts" / "rebuild_frontend.sh",
  ):
    assert "page-[0-9a-f]" not in path.read_text(), path


def test_rebuild_pipeline_rejects_non_sha_live_fingerprint() -> None:
  """rebuild_pipeline.sh must abort instead of comparing unknown==unknown."""
  text = (_REPO_ROOT / "scripts" / "rebuild_pipeline.sh").read_text()
  assert '"${LIVE_FRONTEND_FINGERPRINT}" =~ ^[0-9a-f]{64}$' in text
