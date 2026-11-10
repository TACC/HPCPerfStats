"""proxy.Dockerfile zlib-ng contract: never override zlib-ng CFLAGS on make.

Regression 2026-11-10: ``make ... ../zlib-ng/libz.a CFLAGS=...`` propagated
through MAKEFLAGS and replaced the ``-DWITH_OPTIM -DX86_SSE2 ...`` defines that
zlib-ng's configure writes into its Makefile CFLAGS. functable.c then left
every pointer NULL, so nginx logged ``Zlib-ng functable failed
initialization!`` and ``deflateInit2() failed: -6`` on each gzip response.
"""

from __future__ import annotations

import re
from pathlib import Path


def _proxy_dockerfile() -> str:
  root = Path(__file__).resolve().parents[2]
  return (root / "services-conf" / "proxy.Dockerfile").read_text(
    encoding="utf-8"
  )


def _make_invocations(text: str) -> list[str]:
  """Return each ``make`` command with backslash continuations joined."""
  joined = re.sub(r"\\\n", " ", text)
  return [
    ln.strip() for ln in joined.splitlines() if re.match(r"\s*make\b", ln)
  ]


def zlib_ng_make_cflags_overrides(text: str) -> list[str]:
  """``make`` lines that build zlib-ng libz.a while passing ``CFLAGS=``."""
  return [
    cmd
    for cmd in _make_invocations(text)
    if "libz.a" in cmd and re.search(r"(^|\s)CFLAGS=", cmd)
  ]


def test_zlib_ng_make_never_overrides_cflags() -> None:
  assert zlib_ng_make_cflags_overrides(_proxy_dockerfile()) == []


def test_override_detector_catches_the_2026_11_10_shape() -> None:
  bad = (
    "make -f objs/Makefile -j1 \\\n"
    '  "../openssl-${OPENSSL_VERSION}/.openssl/include/openssl/ssl.h" \\\n'
    "  ../zlib-ng/libz.a \\\n"
    '  CFLAGS="${nginx_bake_cflags}" LDFLAGS="${_lto_ld}"\n'
    'make -j"$(nproc)" libz.a CC=clang CFLAGS="${_cf_bake} -DZLIB_COMPAT"\n'
  )
  assert len(zlib_ng_make_cflags_overrides(bad)) == 2


def test_zlib_ng_wrapper_keeps_native_flags_for_nginx_reconfigure() -> None:
  text = _proxy_dockerfile()
  assert '"export CFLAGS=\\"${_cf_cfg}' in text


def test_nginx_build_probes_deflate_init2_on_linked_libz() -> None:
  text = _proxy_dockerfile()
  assert "deflateInit2(&s,1,Z_DEFLATED,31,8,Z_DEFAULT_STRATEGY)!=Z_OK" in text
  assert "/tmp/zlib_ng_probe.c ../zlib-ng/libz.a -o /tmp/zlib_ng_probe" in text
  probe = text.index("\n/tmp/zlib_ng_probe\n")
  assert (
    text.index("make install CFLAGS=") < probe < text.index("rm -rf /usr/src ")
  )
