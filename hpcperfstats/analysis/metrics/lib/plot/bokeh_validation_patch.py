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

Import this module before ``bokeh.models`` (``update_metrics.py`` does so
right after ``configure_blas_thread_env()``); importing it installs the shim.

Attributes:
  _VALIDATION_ON: Context-local validation flag (default on).
  INSTALLED_BEFORE_BOKEH_MODELS: True when this module was imported before
    ``bokeh.models``, so the import-time version burn was prevented too.
"""

from __future__ import annotations

import contextvars
import sys
from types import TracebackType

from bokeh.core.property import bases, validation

_VALIDATION_ON: contextvars.ContextVar[bool] = contextvars.ContextVar(
  "hpcperfstats_bokeh_validation_on", default=True
)
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


def install_threadsafe_bokeh_property_validation() -> bool:
  """
  Patch Bokeh so validation toggling never writes a class attribute.

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
  return True


install_threadsafe_bokeh_property_validation()
