"""
Regression tests for the thread-safe Bokeh property-validation shim.

The free-threaded update_metrics pool stopped scaling because Bokeh's
``validate`` toggled validation through a class-attribute write on
``Property`` for every model instantiated (type-cache invalidation under the
interpreter-wide type lock; version budget exhausted at import). These tests
pin the shim: no class-attribute write, per-thread scope, idempotent install,
and import order ahead of ``bokeh.models`` in the scheduler.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

bases = pytest.importorskip("bokeh.core.property.bases")
validation = pytest.importorskip("bokeh.core.property.validation")

from hpcperfstats.analysis.metrics.lib.plot import (  # noqa: E402
  bokeh_validation_patch as patch,
)

REPO = Path(__file__).resolve().parents[4]
UPDATE_METRICS_SRC = (
  REPO / "hpcperfstats" / "analysis" / "metrics" / "update_metrics.py"
)
PLOT_INIT_SRC = (
  REPO
  / "hpcperfstats"
  / "analysis"
  / "metrics"
  / "lib"
  / "plot"
  / "__init__.py"
)
PATCH_IMPORT = (
  "import hpcperfstats.analysis.metrics.lib.plot.bokeh_validation_patch"
)


def test_install_is_idempotent_and_replaces_bokeh_hooks():
  assert patch.install_threadsafe_bokeh_property_validation() is False
  assert bases.validation_on is patch.validation_on
  assert validation.validate.__init__ is patch._validate_init
  assert validation.validate.__exit__ is patch._validate_exit


def test_validate_toggle_never_writes_property_class_attribute():
  before = vars(bases.Property).get("_should_validate")
  assert bases.validation_on() is True
  with validation.validate(False):
    assert bases.validation_on() is False
    # The class attribute is untouched: that write was the type-cache killer.
    assert vars(bases.Property).get("_should_validate") == before
  assert bases.validation_on() is True
  assert vars(bases.Property).get("_should_validate") == before


def test_nested_validate_restores_outer_value():
  with validation.validate(False):
    with validation.validate(True):
      assert bases.validation_on() is True
    assert bases.validation_on() is False
  assert bases.validation_on() is True


def test_without_property_validation_decorator_uses_shim():
  seen = []

  @validation.without_property_validation
  def body() -> None:
    seen.append(bases.validation_on())

  body()
  assert seen == [False]
  assert bases.validation_on() is True


def test_validate_false_in_one_thread_does_not_leak_to_another():
  inside = threading.Event()
  release = threading.Event()
  observed: dict[str, bool] = {}

  def holder() -> None:
    with validation.validate(False):
      inside.set()
      release.wait(timeout=10)

  def observer() -> None:
    inside.wait(timeout=10)
    observed["other_thread"] = bases.validation_on()
    release.set()

  threads = [threading.Thread(target=holder), threading.Thread(target=observer)]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=15)
  assert observed == {"other_thread": True}


def test_update_metrics_installs_shim_before_django_and_bokeh_models():
  src = UPDATE_METRICS_SRC.read_text(encoding="utf-8")
  patch_pos = src.find(PATCH_IMPORT)
  assert patch_pos > 0, "update_metrics.py must import the validation shim"
  assert patch_pos < src.find("ensure_django()")
  first_bokeh_models = re.search(r"^\s*(from|import) bokeh\.models", src, re.M)
  assert first_bokeh_models is None or patch_pos < first_bokeh_models.start()


def test_fresh_interpreter_installs_shim_before_bokeh_models_load():
  code = (
    "import hpcperfstats.analysis.metrics.update_metrics\n"
    "from hpcperfstats.analysis.metrics.lib.plot import "
    "bokeh_validation_patch as p\n"
    "from bokeh.core.property import bases\n"
    "print(int(p.INSTALLED_BEFORE_BOKEH_MODELS), "
    "int(bases.validation_on is p.validation_on))\n"
  )
  proc = subprocess.run(
    [sys.executable, "-c", code],
    capture_output=True,
    text=True,
    cwd=str(REPO),
    timeout=600,
    check=False,
  )
  assert proc.returncode == 0, proc.stderr[-2000:]
  assert proc.stdout.strip().splitlines()[-1] == "1 1", proc.stdout[-500:]


def test_plot_package_imports_shim_before_plot_builders():
  src = PLOT_INIT_SRC.read_text(encoding="utf-8")
  patch_pos = src.find(PATCH_IMPORT)
  assert patch_pos > 0
  assert patch_pos < src.find(
    "from hpcperfstats.analysis.metrics.lib.plot.devplot"
  )
