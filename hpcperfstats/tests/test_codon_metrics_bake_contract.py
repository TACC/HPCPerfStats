"""Regression: the update_metrics Codon kernels are baked in codon-build and shipped like the parse pyext."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CODON = _REPO / "hpcperfstats" / "analysis" / "metrics" / "codon"
_BUILD = _CODON / "build_codon_metrics_ext.sh"
_DOCKERFILE = _REPO / "services-conf" / "pipeline_web.Dockerfile"
_SUPERVISORD = _REPO / "services-conf" / "supervisord.conf"
_RUNNER = _REPO / "tests" / "run_dev_container_pytest.sh"
_GITIGNORE = _REPO / ".gitignore"


def _stage_body(dockerfile: str, stage_name: str) -> str:
  match = re.search(
    rf"^FROM .* AS {re.escape(stage_name)}\s*\n(.*?)(?=^FROM |\Z)",
    dockerfile,
    flags=re.MULTILINE | re.DOTALL,
  )
  assert match, f"{stage_name} stage not found in Dockerfile"
  return match.group(1)


def test_compile_unit_is_class_free_and_buffer_only() -> None:
  src = (_CODON / "metrics_kernels.py").read_text(encoding="utf-8")
  assert "\nclass " not in src
  assert "import numpy" not in src
  for sym in (
    "def copy_count_rows_c(",
    "def copy_decode_c(",
    "def group_reduce_c(",
    "def grid_mean_c(",
    "def join_sorted_c(",
  ):
    assert sym in src, sym
  assert src.count("\n@export\ndef ") == 5


def test_pyext_declares_gil_not_used_and_keeps_no_state() -> None:
  src = (_CODON / "pyext_metrics_init.c").read_text(encoding="utf-8")
  assert "Py_MOD_GIL_NOT_USED" in src
  assert "Py_MOD_GIL_USED" not in src.replace("Py_MOD_GIL_NOT_USED", "")
  assert "PyInit_hpcperfstats_codon_metrics" in src
  assert "PyObject_GetBuffer" in src
  assert "PyBuffer_Release" in src
  assert "static PyObject *_cache" not in src


def test_build_script_bakes_native_thinlto_with_ft_smoke_gate() -> None:
  text = _BUILD.read_text(encoding="utf-8")
  assert "set -euo pipefail" in text
  assert "--mcpu=native" in text
  assert "flto=thin" in text
  assert re.search(r"codon build[^\n]*--disable-native", text) is None
  assert "--relocation-model=pic --lib" in text
  assert "-Wl,-rpath,'$ORIGIN'" in text
  assert "patchelf --set-rpath" in text
  assert "codon_metrics_pyext_314t_gil_off=pass" in text
  assert "codon_metrics_pyext_314t=pass" in text
  assert "RTLD_DEEPBIND" in text
  for sym in (
    "copy_count_rows_c",
    "copy_decode_c",
    "group_reduce_c",
    "grid_mean_c",
    "join_sorted_c",
  ):
    assert sym in text, sym


def test_dockerfile_bakes_metrics_pyext_in_codon_build_and_ships_it() -> None:
  text = _DOCKERFILE.read_text(encoding="utf-8")
  codon = _stage_body(text, "codon-build")
  assert (
    "COPY hpcperfstats/analysis/metrics/codon/ /tmp/codon-metrics-src/" in codon
  )
  assert "build_codon_metrics_ext.sh" in codon
  assert "CODON_OUT=/opt/hpcperfstats/codon_metrics" in codon
  assert (
    "test -f /opt/hpcperfstats/codon_metrics/hpcperfstats_codon_metrics.so"
    in codon
  )
  assert (
    "test -f /opt/hpcperfstats/codon_metrics/libhpcperfstats_codon_metrics_kernel.so"
    in codon
  )
  # /opt/codon is removed only after both bakes ran.
  assert codon.index("build_codon_ext.sh") < codon.index(
    "build_codon_metrics_ext.sh"
  )
  assert codon.rindex("rm -rf /tmp/codon-metrics-src /opt/codon") > codon.index(
    "build_codon_metrics_ext.sh"
  )
  base = _stage_body(text, "hpcperfstats-base")
  assert (
    "COPY --from=codon-build /opt/hpcperfstats/codon_metrics /opt/hpcperfstats/codon_metrics"
    in base
  )
  assert (
    "test -f /opt/hpcperfstats/codon_metrics/hpcperfstats_codon_metrics.so"
    in base
  )
  assert (
    "chown -R hpcperfstats:hpcperfstats /opt/hpcperfstats/codon_metrics" in base
  )


def test_supervisord_update_metrics_sees_pyext_dir_single_omp_thread() -> None:
  text = _SUPERVISORD.read_text(encoding="utf-8")
  block = text.split("[program:update_metrics]", 1)[1].split("[program:", 1)[0]
  assert 'PYTHONPATH="/opt/hpcperfstats/codon_metrics"' in block
  assert 'OMP_NUM_THREADS="1"' in block
  assert "/opt/python3.14t/bin/python" in block


def test_dev_runner_exposes_pyext_dir_and_build_mount() -> None:
  text = _RUNNER.read_text(encoding="utf-8")
  assert "/opt/hpcperfstats/codon_metrics" in text
  assert "HPCPERFSTATS_CODON_METRICS_REQUIRED" in text
  assert "HPCPERFSTATS_CODON_METRICS_BUILD_DIR" in text


def test_build_script_is_tracked_despite_so_ignores() -> None:
  gi = _GITIGNORE.read_text(encoding="utf-8")
  assert "!hpcperfstats/analysis/metrics/codon/*.sh" in gi
  proc = subprocess.run(
    ["git", "check-ignore", "-q", str(_BUILD)],
    cwd=_REPO,
    check=False,
    capture_output=True,
  )
  assert proc.returncode == 1, "build script must not be gitignored"
  proc = subprocess.run(
    [
      "git",
      "check-ignore",
      "-q",
      str(_CODON / "hpcperfstats_codon_metrics.so"),
    ],
    cwd=_REPO,
    check=False,
    capture_output=True,
  )
  assert proc.returncode == 0, "built .so must stay gitignored"


def test_readme_documents_bake_and_fail_closed_start() -> None:
  readme = (_CODON / "README.md").read_text(encoding="utf-8")
  assert "codon-build" in readme
  assert "refuses to start" in readme
  assert "metrics_kernels.py" in readme
  assert "pyext_metrics_init.c" in readme
