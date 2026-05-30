"""Set process title (argv) for supervisor daemons so top/ps show script names."""

from __future__ import annotations

import os
import sys

# ``python3 -m hpcperfstats.<module>`` — basename for top when argv[0] is the interpreter.
_MODULE_PROCESS_TITLES: dict[str, str] = {
    "hpcperfstats.seal_syslog_daily": "seal_syslog_daily.py",
    "hpcperfstats.render_syslog_ng_generated": "render_syslog_ng_generated.py",
}


def resolve_script_process_title_name(
    *,
    argv: list[str] | None = None,
    explicit: str | None = None,
) -> str | None:
  """Return a short ``*.py`` title from argv or an explicit name."""
  if explicit:
    name = explicit
  else:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) >= 3 and argv[1] == "-m":
      mod = argv[2]
      short = mod.rsplit(".", 1)[-1]
      name = _MODULE_PROCESS_TITLES.get(mod, f"{short}.py")
    else:
      base = os.path.basename(argv[0] if argv else "")
      if not base or base.startswith("python"):
        return None
      name = base if base.endswith(".py") else f"{base}.py"
  if not name.endswith(".py"):
    name = f"{name}.py"
  return name


def running_under_gunicorn() -> bool:
  """Return True when this process is (or should remain) a gunicorn worker/master."""
  server_software = os.environ.get("SERVER_SOFTWARE", "").strip().lower()
  if server_software.startswith("gunicorn"):
    return True
  try:
    from setproctitle import getproctitle
  except ImportError:
    getproctitle = None
  if getproctitle is not None:
    try:
      title = getproctitle()
      if title.startswith("gunicorn:"):
        return True
    except Exception:
      pass
  for arg in sys.argv[:3]:
    if "gunicorn" in arg:
      return True
  return False


def format_daemon_process_title(
    script_name: str,
    *,
    role: str,
    pool_kind: str | None = None,
) -> str:
  """Build a ``top``/``ps`` title for a daemon main or pool worker process."""
  base = resolve_script_process_title_name(explicit=script_name) or script_name
  if not base.endswith(".py"):
    base = f"{base}.py"
  if role == "worker":
    kind = pool_kind or "pool"
    return f"{base} [worker:{kind}]"
  return f"{base} [{role}]"


def format_daemon_thread_title(script_name: str, *, role: str) -> str:
  """Build a thread title for daemon helper threads (``setthreadtitle`` only)."""
  base = resolve_script_process_title_name(explicit=script_name) or script_name
  if not base.endswith(".py"):
    base = f"{base}.py"
  return f"{base} [thread:{role}]"


def _apply_setproctitle(title: str) -> str:
  try:
    from setproctitle import setproctitle
  except ImportError:
    return title
  try:
    setproctitle(title)
  except Exception:
    pass
  return title


def set_daemon_process_title(
    *,
    name: str | None = None,
    argv: list[str] | None = None,
    role: str = "main",
    pool_kind: str | None = None,
) -> str | None:
  """Set process title for supervisor daemons; no-op under gunicorn."""
  if running_under_gunicorn():
    return None
  script_name = resolve_script_process_title_name(explicit=name, argv=argv)
  if not script_name:
    return None
  title = format_daemon_process_title(
      script_name,
      role=role,
      pool_kind=pool_kind,
  )
  return _apply_setproctitle(title)


def apply_pool_worker_process_title(init_args):
  """Picklable ``multiprocessing.Pool`` initializer for spawn workers."""
  script_name, pool_kind = init_args
  set_daemon_process_title(name=script_name, role="worker", pool_kind=pool_kind)


def set_daemon_thread_title(
    title: str,
    *,
    script_name: str | None = None,
    role: str | None = None,
) -> str:
  """Set the current thread title; does not change the process title."""
  if role is not None:
    if script_name is None:
      script_name = resolve_script_process_title_name()
    if script_name:
      title = format_daemon_thread_title(script_name, role=role)
  try:
    from setproctitle import setthreadtitle
  except ImportError:
    return title
  try:
    setthreadtitle(title)
  except Exception:
    pass
  return title


def set_script_process_title(
    *,
    name: str | None = None,
    argv: list[str] | None = None,
) -> str | None:
  """Set the main daemon process title; delegates to ``set_daemon_process_title``."""
  return set_daemon_process_title(name=name, argv=argv, role="main")
