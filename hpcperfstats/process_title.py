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


def set_script_process_title(
    *,
    name: str | None = None,
    argv: list[str] | None = None,
) -> str | None:
  """Set the process title for top/ps; no-op when setproctitle is unavailable."""
  title = resolve_script_process_title_name(explicit=name, argv=argv)
  if not title:
    return None
  try:
    from setproctitle import setproctitle
  except ImportError:
    return title
  try:
    setproctitle(title)
  except Exception:
    return title
  return title
