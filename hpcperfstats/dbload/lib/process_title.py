"""Set process title (argv) for supervisor daemons so top/ps show script names."""

from __future__ import annotations

import os
import signal
import sys

_PR_SET_PDEATHSIG = 1

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


def _sync_log_role_from_daemon_process(*, role: str, pool_kind: str | None = None) -> None:
  from hpcperfstats.dbload.lib.print_utils import set_log_role

  if role == "worker":
    set_log_role("worker:%s" % (pool_kind or "pool"))
  elif role == "main":
    set_log_role("main")
  elif role:
    set_log_role(role)


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
  _sync_log_role_from_daemon_process(role=role, pool_kind=pool_kind)
  return _apply_setproctitle(title)


def enable_parent_death_signal(sig=None):
  """Linux: deliver *sig* when the pool parent dies (prevents OOM orphan workers)."""
  if sys.platform != "linux":
    return False
  if sig is None:
    sig = signal.SIGKILL
  try:
    import ctypes

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return libc.prctl(_PR_SET_PDEATHSIG, ctypes.c_ulong(sig)) == 0
  except Exception:
    return False


def apply_pool_worker_process_title(script_name, pool_kind):
  """Picklable ``multiprocessing.Pool`` initializer for spawn/fork workers.

  ``Pool`` invokes ``initializer(*initargs)``, so ``initargs`` must be a
  ``(script_name, pool_kind)`` tuple of two positional arguments.
  """
  enable_parent_death_signal()
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
    from hpcperfstats.dbload.lib.print_utils import set_log_role

    set_log_role("thread:%s" % role)
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
