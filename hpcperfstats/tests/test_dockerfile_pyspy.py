"""Dockerfile contracts: temporary py-spy 3.14t cargo pin (PR #860)."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _stage_body(dockerfile: str, stage_name: str) -> str:
  match = re.search(
      rf"^FROM .* AS {re.escape(stage_name)}\s*\n(.*?)(?=^FROM |\Z)",
      dockerfile,
      flags=re.MULTILINE | re.DOTALL,
  )
  assert match, f"{stage_name} stage not found in Dockerfile"
  return match.group(1)


def test_dockerfile_does_not_pip_install_unpinned_pyspy():
  """Released 0.4.2 cannot see libpython3.14t; unpinned GIL pip is the RC."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  assert "python3 -m pip install --no-cache-dir pyinstrument py-spy" not in build
  assert "python3 -m pip install --no-cache-dir pyinstrument" in build
  pip_pyspy_lines = [
      ln for ln in build.splitlines() if "pip install" in ln and "py-spy" in ln
  ]
  assert not pip_pyspy_lines, pip_pyspy_lines


def test_dockerfile_pyspy_temporary_hack_comment_above_cargo_run():
  """Comment immediately above the cargo/source RUN marks the pin as temporary."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  cargo_idx = build.index("ee757909a5698526a7df04687ecbe6d4daad5f8b")
  prefix = build[:cargo_idx]
  comment_block = "\n".join(prefix.splitlines()[-12:])
  assert "TEMPORARY" in comment_block
  assert "#860" in comment_block
  assert "libpython3.14t" in comment_block
  assert "drop" in comment_block.lower() or "official" in comment_block.lower()
  assert ">0.4.2" in comment_block or "0.4.2" in comment_block


def test_dockerfile_pyspy_pins_pr860_tarball_and_comm_patch():
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "ee757909a5698526a7df04687ecbe6d4daad5f8b" in build
  assert "aad4fc01436299b68001120c414d15e88b6c9c53270ea9f9a31ffb060604adce" in build
  assert "honglei/py-spy" in build
  assert "py-spy-314t-comm-names.patch" in build
  assert "cargo build --release --locked" in build
  assert "/opt/python3.14/bin/py-spy" in build


def test_dockerfile_pyspy_bake_smoke_both_abis_no_gil_on_ft():
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "Failed to find python version" in build
  assert "py-spy dump --pid" in build
  assert "/opt/python3.14t/bin/python" in build
  # Never pass --gil on the 3.14t attach smoke.
  ft_region = build[build.index("/opt/python3.14t/bin/python") :]
  assert "--gil" not in ft_region.split("FROM ")[0]


def test_hpcperfstats_base_apt_has_no_rustc_or_cargo():
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  apt_install = base[base.index("apt-get install") : base.index("apt-get clean")]
  assert "rustc" not in apt_install
  assert "cargo" not in apt_install
  assert "libunwind-dev" not in apt_install


def test_pyspy_comm_patch_labels_ft_threads_from_proc_comm():
  patch = (
      _repo_root() / "services-conf" / "py-spy-314t-comm-names.patch"
  ).read_text()
  assert "/proc/" in patch and "task" in patch and "comm" in patch
  assert "free_threaded" in patch
  assert "os_thread_id" in patch
