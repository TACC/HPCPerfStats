"""Contract tests for musl GCC 16.2 toolchain image and version gate script."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _embedded_assert_from_gcc_alpine() -> str:
  text = (_repo_root() / "services-conf" / "gcc-alpine.Dockerfile").read_text()
  match = re.search(
    r"COPY <<'ASSERT_GCC_EOF' /usr/local/bin/assert_gcc_min_version\.sh\n"
    r"(.*?)\nASSERT_GCC_EOF",
    text,
    re.DOTALL,
  )
  assert match is not None, (
    "gcc-alpine.Dockerfile must embed ASSERT_GCC_EOF heredoc"
  )
  return match.group(1) + "\n"


def test_gcc_alpine_dockerfile_pins_version_and_prefix() -> None:
  text = (_repo_root() / "services-conf" / "gcc-alpine.Dockerfile").read_text()
  assert "ARG ALPINE_VERSION=3.24.2" in text
  assert "ARG GCC_VERSION=16.2.0" in text
  assert (
    "ARG GCC_SHA256=e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e"
    in text
  )
  assert "ARG GCC_MIN_VERSION=16.2" in text
  assert "--prefix=/opt/gcc-16" in text
  assert "COPY --from=gcc-builder /opt/gcc-16 /opt/gcc-16" in text
  assert "COPY <<'ASSERT_GCC_EOF'" in text
  assert "COPY assert_gcc_min_version.sh" not in text
  assert "COPY services-conf/assert_gcc_min_version.sh" not in text


def test_gcc_alpine_embedded_assert_matches_canonical_script() -> None:
  canonical = (
    _repo_root() / "services-conf" / "assert_gcc_min_version.sh"
  ).read_text()
  embedded = _embedded_assert_from_gcc_alpine()
  assert embedded == canonical


def test_rebuild_full_site_builds_musl_gcc_with_services_conf_context() -> None:
  text = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  assert (
    'podman build -f "${GCC_ALPINE_DOCKERFILE}" -t "${GCC_MUSL_IMAGE}" services-conf'
    in text
  )
  assert "services-conf/assert_gcc_min_version.sh" in text


def test_assert_gcc_min_version_script_uses_dumpfullversion() -> None:
  text = (
    _repo_root() / "services-conf" / "assert_gcc_min_version.sh"
  ).read_text()
  assert "gcc -dumpfullversion" in text
  assert "sort -V" in text
  assert "sort -C -V" not in text
  assert "GCC_MIN_VERSION" in text
