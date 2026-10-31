"""
Thread-safe replacement for Bokeh's property-validation toggle.

Bokeh's ``validate`` context manager (``bokeh.core.property.validation``)
switches validation by writing the class attribute
``Property._should_validate`` on enter and exit, and ``HasProps._initialize``
does that for every model instantiated. Each class-attribute write
invalidates the type-attribute cache of ``Property`` and every subclass; on
the free-threaded interpreter that runs under the interpreter-wide type lock,
and after 1000 writes (reached while importing ``bokeh.models``) the classes
can never be cached again, so all Bokeh property lookups serialize on that
lock and the update_metrics thread pool stops scaling. This module keeps the
flag in a ``contextvars.ContextVar`` instead: no class attribute is written,
and the toggle is scoped to the current thread rather than the whole process.

A second hot spot shows up once the type lock is gone:
``PropertyDescriptor.is_unstable`` runs two ``import`` statements per call
(``from types import FunctionType``, ``from .instance import
InstanceDefault``), and on the free-threaded build every import statement
takes the shared ``sys.modules`` dict lock, which was 34% of contended lock
acquisitions at 28 threads. The shim replaces it with the same check against
types resolved once at import.

Import this module before ``bokeh.models`` (``update_metrics.py`` does so
right after ``configure_blas_thread_env()``); importing it installs the shim.

Attributes:
  _VALIDATION_ON: Context-local validation flag (default on).
  _UNSTABLE_TYPES: Types ``is_unstable`` recognises as callable defaults.
  INSTALLED_BEFORE_BOKEH_MODELS: True when this module was imported before
    ``bokeh.models``, so the import-time version burn was prevented too.
"""

from __future__ import annotations

import contextvars
import sys
from types import FunctionType, TracebackType
from typing import Any

from bokeh.core.property import bases, descriptors, validation
from bokeh.core.property.instance import InstanceDefault

_VALIDATION_ON: contextvars.ContextVar[bool] = contextvars.ContextVar(
  "hpcperfstats_bokeh_validation_on", default=True
)
_UNSTABLE_TYPES: tuple[type, ...] = (FunctionType, InstanceDefault)
INSTALLED_BEFORE_BOKEH_MODELS: bool = "bokeh.models" not in sys.modules


def validation_on() -> bool:
  """
  Report whether Bokeh property validation is on in the current context.

  Replaces ``bokeh.core.property.bases.validation_on`` so Bokeh's
  ``prepare_value`` reads the context variable instead of the class flag.

  Returns:
    bool: True unless the current context is inside ``validate(False)``.

  Examples:
    >>> validation_on()
    True
  """
  return _VALIDATION_ON.get()


def _validate_init(self: validation.validate, value: bool) -> None:
  """
  Replacement ``validate.__init__``: set the flag without touching the class.

  Args:
    self (validation.validate): The context-manager instance.
    value (bool): Whether validation should run inside the block.

  Returns:
    None

  Examples:
    >>> with validation.validate(False):
    ...   validation_on()
    False
  """
  self.old = _VALIDATION_ON.get()
  _VALIDATION_ON.set(bool(value))


def _validate_exit(
  self: validation.validate,
  typ: type[BaseException] | None,
  value: BaseException | None,
  traceback: TracebackType | None,
) -> None:
  """
  Replacement ``validate.__exit__``: restore the flag saved on enter.

  Args:
    self (validation.validate): The context-manager instance.
    typ (type[BaseException] | None): Exception type leaving the block.
    value (BaseException | None): Exception instance leaving the block.
    traceback (TracebackType | None): Traceback leaving the block.

  Returns:
    None

  Examples:
    >>> with validation.validate(False):
    ...   pass
    >>> validation_on()
    True
  """
  del typ, value, traceback
  _VALIDATION_ON.set(self.old)


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


def install_threadsafe_bokeh_property_validation() -> bool:
  """
  Patch Bokeh so validation toggling never writes a class attribute, and
  ``is_unstable`` stops importing on every call.

  Idempotent: the second call is a no-op.

  Returns:
    bool: True when the shim was installed by this call, False when it was
    already in place.

  Examples:
    >>> install_threadsafe_bokeh_property_validation()
    False
  """
  if bases.validation_on is validation_on:
    return False
  bases.validation_on = validation_on
  validation.validate.__init__ = _validate_init
  validation.validate.__exit__ = _validate_exit
  descriptors.PropertyDescriptor.is_unstable = classmethod(_is_unstable)
  return True


install_threadsafe_bokeh_property_validation()
