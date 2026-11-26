"""
Free-threaded hot-path shim for Bokeh's ``PropertyDescriptor.is_unstable``.

Stock ``PropertyDescriptor.is_unstable`` runs two ``import`` statements per
call (``from types import FunctionType``, ``from .instance import
InstanceDefault``), and on the free-threaded build every import statement
takes the shared ``sys.modules`` dict lock, which was 34% of contended lock
acquisitions at 28 threads in the update_metrics pool. The shim replaces it
with the same check against types resolved once at import.

Property validation needs no patch: Bokeh 3.10.2+ keeps the toggle in a
``ContextVar`` (``bokeh.core.property.bases._validation_override``) and never
writes a ``Property`` class attribute.

Import this module before ``bokeh.models`` (``update_metrics.py`` does so
right after ``configure_blas_thread_env()``); importing it installs the shim.

Attributes:
  _UNSTABLE_TYPES: Types ``is_unstable`` recognises as callable defaults.
  INSTALLED_BEFORE_BOKEH_MODELS: True when this module was imported before
    ``bokeh.models``, so model construction at import already used the shim.
"""

from __future__ import annotations

import sys
from types import FunctionType
from typing import Any

from bokeh.core.property import descriptors
from bokeh.core.property.instance import InstanceDefault

_UNSTABLE_TYPES: tuple[type, ...] = (FunctionType, InstanceDefault)
INSTALLED_BEFORE_BOKEH_MODELS: bool = "bokeh.models" not in sys.modules


def _is_unstable(cls: type, value: Any) -> bool:
  """
  Replacement ``PropertyDescriptor.is_unstable`` without per-call imports.

  Args:
    cls (type): The descriptor class (classmethod receiver, unused).
    value (Any): A property default value.

  Returns:
    bool: True when ``value`` is a function or an ``InstanceDefault``, the
    defaults Bokeh evaluates per instance.

  Examples:
    >>> _is_unstable(object, lambda: 1), _is_unstable(object, 5)
    (True, False)
  """
  del cls
  return isinstance(value, _UNSTABLE_TYPES)


def install_bokeh_is_unstable_shim() -> bool:
  """
  Patch ``PropertyDescriptor.is_unstable`` so it stops importing per call.

  Idempotent: the second call is a no-op.

  Returns:
    bool: True when the shim was installed by this call, False when it was
    already in place.

  Examples:
    >>> install_bokeh_is_unstable_shim()
    False
  """
  current = descriptors.PropertyDescriptor.__dict__["is_unstable"]
  if isinstance(current, classmethod) and current.__func__ is _is_unstable:
    return False
  descriptors.PropertyDescriptor.is_unstable = classmethod(_is_unstable)
  return True


install_bokeh_is_unstable_shim()
