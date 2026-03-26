import builtins

from hpcperfstats.print_utils import _script_prefix, log_print


def test_script_prefix_uses_main_file(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(__import__("sys").modules, "__main__", DummyMain)
  assert _script_prefix() == "[sync_timedb]"


def test_log_print_prefixes_output(monkeypatch, capsys):
  # Force a deterministic prefix
  class DummyMain:
    __file__ = "/tmp/tool.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)

  calls = []

  def fake_print(*args, **kwargs):
    calls.append((args, kwargs))

  monkeypatch.setattr(builtins, "print", fake_print)

  log_print("hello", 123, end="!")

  assert calls, "log_print should call print()"
  (args, kwargs) = calls[0]
  # First arg is the prefix, remaining are passthrough
  assert args[0] == "[tool]"
  assert args[1:] == ("hello", 123)
  assert kwargs.get("end") == "!"

