"""Dockerfile contracts: SHA-pinned py-spy that can dump GIL 3.14 and FT 3.14t."""

from __future__ import annotations

import re
from pathlib import Path

# PR #860 HEAD used for the cargo pin (honglei/py-spy). Bump with the Dockerfile.
_PYSPY_PIN_SHA = "ee757909a5698526a7df04687ecbe6d4daad5f8b"
_PYSPY_TARBALL_SHA256 = (
    "aad4fc01436299b68001120c414d15e88b6c9c53270ea9f9a31ffb060604adce"
)


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


def _python_build() -> str:
  return _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")


def _hpcperfstats_base() -> str:
  return _stage_body(
      (_repo_root() / "Dockerfile").read_text(),
      "hpcperfstats-base",
  )


def test_python_build_does_not_pip_install_unpinned_pyspy():
  """Released PyPI 0.4.2 cannot see libpython3.14t; do not install it via pip."""
  build = _python_build()
  assert "python3 -m pip install --no-cache-dir pyinstrument py-spy" not in build
  assert "python3 -m pip install --no-cache-dir pyinstrument" in build
  pip_lines = [
      ln
      for ln in build.splitlines()
      if "python3 -m pip install" in ln and "pyinstrument" in ln
  ]
  assert pip_lines
  assert all("py-spy" not in ln for ln in pip_lines)


def test_python_build_pyspy_pins_pr_860_sha_and_temporary_comment():
  """Cargo pin must be SHA-locked, TEMPORARY, and dropped when official >0.4.2 ships."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  assert _PYSPY_PIN_SHA in build
  assert _PYSPY_TARBALL_SHA256 in build
  assert f"honglei/py-spy/archive/{_PYSPY_PIN_SHA}.tar.gz" in build
  assert "sha256sum -c" in build
  assert "cargo build --release --locked" in build
  assert "install -m 0755" in build and "/opt/python3.14/bin/py-spy" in build
  assert "libpython3.14t" in build
  assert "1.88.0" in build
  assert "libunwind-dev" in build
  # TEMPORARY / #860 / drop-when-official live immediately above the cargo RUN.
  cargo_idx = dockerfile.index("cargo build --release --locked")
  run_start = dockerfile.rfind("RUN /bin/bash", 0, cargo_idx)
  comment_window = dockerfile[max(0, run_start - 600) : cargo_idx]
  assert "TEMPORARY" in comment_window
  assert "#860" in comment_window or "PR #860" in comment_window
  assert "0.4.2" in comment_window
  assert "libpython3.14t" in comment_window


def test_python_build_pyspy_dump_smoke_covers_gil_and_314t_without_gil_flag():
  """Bake must fail closed on version-detect; podman build EPERM is not that failure.

  Regression (hpcperfstats01 2026-09-14): py-spy found Py_Version then
  ``Failed to copy Py_Version symbol`` / Permission denied under buildah
  seccomp. That must not fail the image; ``Failed to find python version``
  still must. Binary must contain ``libpython3.14t``.
  """
  build = _python_build()
  assert "py-spy dump --pid" in build
  assert "Failed to find python version" in build
  assert "Failed to copy Py_Version" in build
  assert "Permission denied" in build
  assert "PYSPY_BUILD_PTRACE_UNAVAILABLE" in build
  assert "grep -aF libpython3.14t" in build
  assert "python3" in build
  assert "/opt/python3.14t/bin/python" in build
  dump_lines = [ln for ln in build.splitlines() if "py-spy dump" in ln]
  assert dump_lines
  assert all("--gil" not in ln for ln in dump_lines)
  assert "time.sleep" in build
  assert "py-spy --version" in build
  # Must not treat dump EPERM as a hard bake failure (old ``|| { … false }``).
  assert "|| { echo \"$gout\"; kill" not in build
  assert "|| { echo \"$tout\"; kill" not in build


def test_python_build_wipes_pyspy_src_and_rustup_before_stage_end():
  """rustc/cargo must not leak via leftover trees; runtime COPY is /opt only."""
  build = _python_build()
  assert "test ! -d /usr/src/py-spy" in build
  assert 'rm -rf /usr/src/py-spy' in build or "rm -rf /usr/src/py-spy " in build
  assert "$HOME/.cargo" in build
  assert "$HOME/.rustup" in build


def test_hpcperfstats_base_has_libunwind8_not_rustc_or_libunwind_dev():
  """Dynamically linked py-spy needs libunwind8; compilers stay in python-build."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  base = _stage_body(dockerfile, "hpcperfstats-base")
  base_apt = base[base.index("apt-get install") : base.index("apt-get clean")]
  assert "rustc" not in base_apt
  assert "cargo" not in base_apt
  assert "rustup" not in base
  assert "libunwind-dev" not in base_apt
  assert re.search(r"\blibunwind8\b", base_apt), base_apt
  assert "libunwind-dev" in build
  copies = [
      ln.strip()
      for ln in base.splitlines()
      if ln.strip().startswith("COPY --from=python-build")
  ]
  assert any(ln.endswith("/opt/python3.14") or "/opt/python3.14 /opt/python3.14" in ln for ln in copies)
  assert all("/usr/src" not in ln and ".cargo" not in ln for ln in copies)
