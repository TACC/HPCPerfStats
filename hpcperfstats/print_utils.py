"""Uniform script-prefixed print for all HPCPerfStats scripts.

Use log_print() instead of print() so every message is prefixed with [script_name]
of the original calling tool (the script that was run). Library code that is only
imported uses the same label as the script that invoked it (e.g. [sync_timedb]).
"""
import inspect
import sys


def _script_prefix():
  """Return [scriptname] for the original entry point (__main__), not the immediate caller."""
  main = sys.modules.get("__main__")
  if main is not None and getattr(main, "__file__", None):
    path = main.__file__
  else:
    # Fallback: use immediate caller (e.g. interactive interpreter)
    frame = inspect.stack()[2]
    path = frame.filename
  name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
  return f"[{name}]"


def log_print(*args, **kwargs):
  """Print with script prefix. Same signature as print(); forwards all kwargs (e.g. file=, flush=)."""
  prefix = _script_prefix()
  return print(prefix, *args, **kwargs)
