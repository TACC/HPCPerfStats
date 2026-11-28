"""tests/compose_test_cmd.sh refuses a stale local test overlay copy."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "tests" / "docker-compose.test-overlay.yaml.example"


def _ensure(repo: Path) -> subprocess.CompletedProcess[str]:
  """Run compose_ensure_test_overlay_yaml against a fake checkout.

  Args:
    repo (Path): Directory holding ``tests/`` with the example (and copy).

  Returns:
    subprocess.CompletedProcess[str]: The bash run.
  """
  script = (REPO / "tests" / "compose_test_cmd.sh").read_text()
  start = script.index("compose_ensure_test_overlay_yaml() {")
  end = script.index("\n}\n", start) + 3
  body = (
    f"compose_repo_root() {{ echo '{repo}'; }}\n"
    f"{script[start:end]}\ncompose_ensure_test_overlay_yaml && echo ENSURED"
  )
  return subprocess.run(
    ["bash", "-c", body], capture_output=True, text=True, check=False
  )


def _fake_repo(tmp_path: Path) -> Path:
  (tmp_path / "tests").mkdir()
  shutil.copy(EXAMPLE, tmp_path / "tests" / EXAMPLE.name)
  return tmp_path


def test_example_is_versioned_and_maps_db_to_pg18_mount() -> None:
  text = EXAMPLE.read_text()
  assert text.splitlines()[0] == "# overlay-version: 2"
  assert "/var/lib/postgresql/data" not in text
  assert "test_postgres_data_pg18:/var/lib/postgresql" in text
  assert "  db_pg18:" not in text


def test_missing_copy_is_created_from_example(tmp_path: Path) -> None:
  repo = _fake_repo(tmp_path)
  res = _ensure(repo)
  assert res.returncode == 0, res.stderr
  assert "ENSURED" in res.stdout
  copy = repo / "tests" / "docker-compose.test-overlay.yaml"
  assert copy.read_text() == EXAMPLE.read_text()


def test_current_copy_with_local_edits_is_accepted(tmp_path: Path) -> None:
  repo = _fake_repo(tmp_path)
  copy = repo / "tests" / "docker-compose.test-overlay.yaml"
  copy.write_text(EXAMPLE.read_text() + "# local edit\n")
  res = _ensure(repo)
  assert res.returncode == 0, res.stderr
  assert copy.read_text().endswith("# local edit\n")


def test_stale_unversioned_copy_is_refused_and_left_untouched(
  tmp_path: Path,
) -> None:
  """Regression: the PG15-era copy maps db to /var/lib/postgresql/data."""
  repo = _fake_repo(tmp_path)
  copy = repo / "tests" / "docker-compose.test-overlay.yaml"
  stale = (
    "services:\n  db:\n    volumes:\n      - x:/var/lib/postgresql/data/\n"
  )
  copy.write_text(stale)
  res = _ensure(repo)
  assert res.returncode == 1
  assert "ENSURED" not in res.stdout
  assert "merge your local edits into a fresh copy of" in res.stderr
  assert "tests/docker-compose.test-overlay.yaml.example" in res.stderr
  assert copy.read_text() == stale


def test_older_version_copy_is_refused(tmp_path: Path) -> None:
  repo = _fake_repo(tmp_path)
  copy = repo / "tests" / "docker-compose.test-overlay.yaml"
  copy.write_text("# overlay-version: 1\nservices: {}\n")
  res = _ensure(repo)
  assert res.returncode == 1
  assert "overlay-version: 1" in res.stderr
