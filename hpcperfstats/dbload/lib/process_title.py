"""
Set process title (argv) for supervisor daemons so top/ps show script names.

Attributes:
  _MODULE_PROCESS_TITLES: Attribute.
  _PR_SET_PDEATHSIG: Attribute.
"""

from __future__ import annotations

from typing import Any

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
  """
  Return a short ``*.py`` title from argv or an explicit name.
  
  Args:
    argv (list[str] | None): One of ``list[str]``, ``None``.
    explicit (str | None): One of ``str``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> resolve_script_process_title_name(None, None)  # doctest: +SKIP
  """
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
  """
  Return True when this process is (or should remain) a gunicorn worker/master.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> running_under_gunicorn()  # doctest: +SKIP
  """
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
  """
  Build a ``top``/``ps`` title for a daemon main or pool worker process.
  
  Args:
    script_name (str): String for script name.
    role (str): String for role.
    pool_kind (str | None): One of ``str``, ``None``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> format_daemon_process_title("x", "x", None)  # doctest: +SKIP
  """
  base = resolve_script_process_title_name(explicit=script_name) or script_name
  if not base.endswith(".py"):
    base = f"{base}.py"
  if role == "worker":
    kind = pool_kind or "pool"
    return f"{base} [worker:{kind}]"
  return f"{base} [{role}]"


def format_daemon_thread_title(script_name: str, *, role: str) -> str:
  """
  Build a thread title for daemon helper threads (``setthreadtitle`` only).
  
  Args:
    script_name (str): String for script name.
    role (str): String for role.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> format_daemon_thread_title("x", "x")  # doctest: +SKIP
  """
  base = resolve_script_process_title_name(explicit=script_name) or script_name
  if not base.endswith(".py"):
    base = f"{base}.py"
  return f"{base} [thread:{role}]"


def _apply_setproctitle(title: str) -> str:
  """
  Internal helper to apply the setproctitle.
  
  Args:
    title (str): String for title.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _apply_setproctitle("x")  # doctest: +SKIP
  """
  try:
    from setproctitle import setproctitle
  except ImportError:
    return title
  try:
    setproctitle(title)
  except Exception:
    pass
  return title


def _sync_log_role_from_daemon_process(
  *,
  role: str,
  pool_kind: str | None = None,
) -> None:
  """
  Internal helper to sync the log role from daemon process.
  
  Args:
    role (str): String for role.
    pool_kind (str | None): One of ``str``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> _sync_log_role_from_daemon_process("x", None)  # doctest: +SKIP
  """
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
  """
  Set process title for supervisor daemons; no-op under gunicorn.
  
  Args:
    name (str | None): One of ``str``, ``None``.
    argv (list[str] | None): One of ``list[str]``, ``None``.
    role (str): String for role.
    pool_kind (str | None): One of ``str``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> set_daemon_process_title(None, None, "x", None)  # doctest: +SKIP
  """
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


def enable_parent_death_signal(sig: Any | None = None) -> Any:
  """
  Linux: deliver *sig* when the pool parent dies (prevents OOM orphan workers).
  
  Args:
    sig (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Open return polymorphism from ``enable_parent_death_signal``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> enable_parent_death_signal(None)  # doctest: +SKIP
  """
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


def apply_pool_worker_process_title(script_name: Any, pool_kind: Any) -> None:
  """
  Picklable ``multiprocessing.Pool`` initializer for spawn/fork workers.

  ``Pool`` invokes ``initializer(*initargs)``, so ``initargs`` must be a
  ``(script_name, pool_kind)`` tuple of two positional arguments.

  Resets ``SIGTERM`` / ``SIGINT`` to ``SIG_DFL`` so workers do not inherit a
  parent daemon handler that only sets a shutdown flag (hs04: stdlib
  ``Pool.terminate`` hung forever at ``p.join()`` after ineffective SIGTERM).

  Args:
    script_name (Any): Daemon script basename for the process title.
    pool_kind (Any): Stable pool label (e.g. ``metrics-pool``).

  Returns:
    None

  Examples:
    >>> apply_pool_worker_process_title(
    ...     "update_metrics.py", "metrics-pool"
    ... )  # doctest: +SKIP
  """
  try:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
  except Exception:
    pass
  enable_parent_death_signal()
  set_daemon_process_title(name=script_name, role="worker", pool_kind=pool_kind)


def set_daemon_thread_title(
  title: str,
  *,
  script_name: str | None = None,
  role: str | None = None,
) -> str:
  """
  Set the current thread title; does not change the process title.
  
  Args:
    title (str): String for title.
    script_name (str | None): One of ``str``, ``None``.
    role (str | None): One of ``str``, ``None``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> set_daemon_thread_title("x", None, None)  # doctest: +SKIP
  """
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
  """
  Set the main daemon process title; delegates to ``set_daemon_process_title``.
  
  Args:
    name (str | None): One of ``str``, ``None``.
    argv (list[str] | None): One of ``list[str]``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> set_script_process_title(None, None)  # doctest: +SKIP
  """
  return set_daemon_process_title(name=name, argv=argv, role="main")
