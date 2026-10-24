"""Regression: Codon pyext is built in Dockerfile codon-build and shipped with $ORIGIN runtime."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BUILD = _REPO / "hpcperfstats" / "dbload" / "codon" / "build_codon_ext.sh"
_DOCKERFILE = _REPO / "services-conf" / "pipeline_web.Dockerfile"
_GITIGNORE = _REPO / ".gitignore"


def _stage_body(dockerfile: str, stage_name: str) -> str:
  match = re.search(
    rf"^FROM .* AS {re.escape(stage_name)}\s*\n(.*?)(?=^FROM |\Z)",
    dockerfile,
    flags=re.MULTILINE | re.DOTALL,
  )
  assert match, f"{stage_name} stage not found in Dockerfile"
  return match.group(1)


def test_build_script_is_in_image_only_no_podman_host_bake() -> None:
  text = _BUILD.read_text(encoding="utf-8")
  assert "libcodonrt.so" in text
  assert "libomp.so" in text
  assert "patchelf" in text
  assert (
    '-L"${CODON_OUT}" -lcodonrt' in text or '-L"${CODON_OUT}" -lcodonrt' in text
  )
  assert "patchelf --set-rpath '$ORIGIN'" in text
  assert "podman" not in text
  assert "codon_parse_image" not in text
  assert "Py_MOD_GIL_NOT_USED" in text or "GIL_NOT_USED" in text
  assert "codon_pyext_314t_gil_off=pass" in text
  assert "simd_insn_count zmm=" in text
  assert "grep -A1 -E" in text
  assert "target-cpu|tune-cpu" in text
  # Smoke must not put a Codon install tree on LD_LIBRARY_PATH.
  smoke = text.split("FT 3.14t import+fingerprint smoke")[1].split(
    "codon_pyext_314t=pass"
  )[0]
  assert 'LD_LIBRARY_PATH="/opt/zlib-ng/lib:/opt/python3.14t/lib"' in smoke
  assert "/opt/codon/lib" not in smoke
  assert "sys._is_gil_enabled()" in smoke or "_is_gil_enabled" in smoke


def test_dockerfile_codon_build_stage_copies_to_runtime() -> None:
  text = _DOCKERFILE.read_text(encoding="utf-8")
  codon = _stage_body(text, "codon-build")
  assert "FROM python-build AS codon-build" in text or "AS codon-build" in text
  assert "CODON_VERSION=0.20.4" in codon
  assert "CODON_TARBALL_SHA256=" in codon
  assert "sha256sum -c" in codon
  assert "build_codon_ext.sh" in codon
  assert "CODON_OUT=/opt/hpcperfstats/codon_parse" in codon
  assert "--mcpu=native" in _BUILD.read_text(encoding="utf-8")
  assert "flto=thin" in _BUILD.read_text(encoding="utf-8")
  base = _stage_body(text, "hpcperfstats-base")
  assert "COPY --from=codon-build /opt/hpcperfstats/codon_parse" in base
  assert "HPCPERFSTATS_CODON_CACHEBUST" in base
  assert "test -f /opt/hpcperfstats/codon_parse/libcodonrt.so" in base
  assert "codon_parse_image" not in text
  assert "build_codon_ext_in_pipeline.sh" not in text


def test_rebuild_helper_passes_codon_cachebust_build_arg() -> None:
  helpers = (
    _REPO / "scripts" / "lib" / "compose_frontend_helpers.sh"
  ).read_text(encoding="utf-8")
  assert "HPCPERFSTATS_CODON_CACHEBUST" in helpers
  assert "--build-arg" in helpers


def test_versioned_shared_object_sonames_are_gitignored() -> None:
  """*.so alone misses libgcc_s.so.1 / libgfortran.so.5."""
  gi = _GITIGNORE.read_text(encoding="utf-8")
  assert "*.so.*" in gi
  assert "!hpcperfstats/dbload/codon/*.sh" in gi
  assert "codon_parse_image" not in gi
  samples = (
    _REPO / "hpcperfstats" / "dbload" / "codon" / "hpcperfstats_codon_parse.so",
    _REPO / "libgcc_s.so.1",
    _REPO / "libgfortran.so.5",
  )
  for path in samples:
    proc = subprocess.run(
      ["git", "check-ignore", "-q", str(path)],
      cwd=_REPO,
      check=False,
    )
    assert proc.returncode == 0, f"expected ignore for {path}"
  proc_build = subprocess.run(
    ["git", "check-ignore", "-q", str(_BUILD)],
    cwd=_REPO,
    check=False,
  )
  assert proc_build.returncode == 1, "in-image build script must be trackable"
