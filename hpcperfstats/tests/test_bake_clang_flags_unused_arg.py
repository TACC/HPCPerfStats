"""bake_clang_flags.sh: link-only flags must not spam -Wunused-command-line-argument.

Regression 2026-11-10: ``-fuse-ld=lld`` / ``--ld-path=...`` ride in CC/CXX and
in the PostgreSQL CFLAGS so every link uses lld, so each compile-only ``-c``
call printed ``clang: warning: argument unused during compilation`` during the
db/proxy bakes. Every flag string that carries a link-only flag must also carry
``-Wno-unused-command-line-argument``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_NO_WARN = "-Wno-unused-command-line-argument"
_LINK_ONLY = re.compile(r"(-fuse-ld=|--ld-path=)")


def _services_conf() -> Path:
  return Path(__file__).resolve().parents[2] / "services-conf"


def _bake_eval(expr: str) -> str:
  script = _services_conf() / "bake_clang_flags.sh"
  out = subprocess.run(
    ["bash", "-c", f'. "{script}"; {expr}'],
    check=True,
    capture_output=True,
    text=True,
  )
  return out.stdout


@pytest.mark.parametrize(
  "expr",
  [
    "hpcperfstats_native_base_cflags pg",
    "hpcperfstats_alpine_thinlto_ldflags",
    'hpcperfstats_alpine_export_clang_link_toolchain; printf "%s" "$CC"',
    'hpcperfstats_alpine_export_clang_link_toolchain; printf "%s" "$CXX"',
  ],
)
def test_link_only_flag_strings_silence_unused_arg(expr: str) -> None:
  flags = _bake_eval(expr)
  assert _LINK_ONLY.search(flags), flags
  assert _NO_WARN in flags.split(), flags


def test_dockerfile_flag_envs_with_link_only_flags_silence_unused_arg() -> None:
  env_line = re.compile(
    r"^\s*(?:ENV\s+)?(OPT_CFLAGS_\w+|HPC_CLANG_LD_PATH)=\"?([^\"\n]*)",
    re.MULTILINE,
  )
  hits = 0
  for dockerfile in sorted(_services_conf().glob("*.Dockerfile")):
    for name, value in env_line.findall(dockerfile.read_text(encoding="utf-8")):
      if _LINK_ONLY.search(value):
        hits += 1
        assert _NO_WARN in value.split(), f"{dockerfile.name} {name}={value}"
  assert hits >= 4
