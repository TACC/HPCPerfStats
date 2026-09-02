"""Unit tests for pipeline daemon Python ABI startup fingerprint."""

from __future__ import annotations

from hpcperfstats.dbload.lib.python_abi_startup_log import (
    format_python_abi_startup_line,
    log_python_abi_startup,
)


def test_format_python_abi_startup_line_includes_executable_and_gil_fields():
  line = format_python_abi_startup_line()
  assert line.startswith("python_abi executable=")
  assert "Py_GIL_DISABLED=" in line
  assert "sys._is_gil_enabled=" in line


def test_log_python_abi_startup_emits_once_via_printer():
  logged: list[str] = []

  def _capture(msg: str, flush: bool = False) -> None:
    logged.append(msg)
    assert flush is True

  out = log_python_abi_startup(printer=_capture)
  assert out.startswith("python_abi executable=")
  assert logged == [out]
