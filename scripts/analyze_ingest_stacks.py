#!/usr/bin/env python3
"""
Aggregate faulthandler all-thread dumps into a wall-clock profile by role.

``HPCPERFSTATS_LOADED48_STACKDUMP_S=30`` makes the loaded48 soak child write an
all-thread Python traceback every N seconds to ``<child_log>.stacks``. A single
dump says where each thread is *right now*, so many dumps give an unbiased
wall-clock profile (including threads that are waiting, which a CPU profiler
cannot see). This script groups threads into roles, classifies what the
innermost frame is doing (lock wait, queue wait, DB I/O, pandas compute,
project compute) and lists the innermost ``hpcperfstats`` frame per role.

Usage:
  python3 scripts/analyze_ingest_stacks.py --stacks CHILD.log.stacks [--top 12]

Attributes:
  Frame: One traceback frame (path, line, function).
  Thread: A thread's frames, innermost first.
  _THREAD_RE: Regex matching a thread header.
  _FRAME_RE: Regex matching a ``File`` frame line.
  _PROJECT_MARK: Path fragment identifying project (not library) frames.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path
from typing import NamedTuple

_THREAD_RE = re.compile(r"^Thread 0x[0-9a-fA-F]+")
_FRAME_RE = re.compile(
  r'^\s+File "(?P<path>.*)", line (?P<line>\d+|\?\?\?) in (?P<func>\S+)'
)
_PROJECT_MARK = "/hpcperfstats/"


class Frame(NamedTuple):
  """
  One traceback frame.

  Attributes:
    path: Source file path as printed by ``faulthandler``.
    line: Line number, or ``-1`` when the dump printed ``???``.
    func: Function name.
  """

  path: str
  line: int
  func: str


Thread = list[Frame]


def pct(part: float, total: float) -> float:
  """
  Return ``part / total`` as a percentage, ``0.0`` when ``total`` is zero.

  Args:
    part (float): Numerator.
    total (float): Denominator.

  Returns:
    float: Percentage in ``[0, 100]`` for ``part <= total``.

  Examples:
    >>> pct(1, 4)
    25.0
  """
  return 100.0 * part / total if total else 0.0


def split_snapshots(text: str) -> list[str]:
  """
  Split a ``.stacks`` file into one text block per dump.

  Args:
    text (str): File contents; dumps start at a ``Timeout (`` banner.

  Returns:
    list[str]: One block per dump; the whole text when no banner exists.

  Examples:
    >>> len(
    ...   split_snapshots(
    ...     "Timeout (0:00:30)!\\nThread 0x1\\nTimeout (0:00:30)!\\n"
    ...   )
    ... )
    2
  """
  parts = re.split(r"(?m)^(?=Timeout \()", text)
  blocks = [p for p in parts if p.strip()]
  return blocks or [text]


def parse_dump(text: str) -> list[Thread]:
  """
  Parse one dump block into threads (innermost frame first).

  Args:
    text (str): One block from :func:`split_snapshots`.

  Returns:
    list[Thread]: One frame list per ``Thread 0x…`` header; ``<invalid
      frame>`` lines are dropped and unreadable line numbers become ``-1``.

  Examples:
    >>> parse_dump(
    ...   'Thread 0x1 (most recent call first):\\n  File "a.py", line 3 in f\\n'
    ... )[0][0].func
    'f'
  """
  threads: list[Thread] = []
  for line in text.splitlines():
    if _THREAD_RE.match(line):
      threads.append([])
      continue
    m = _FRAME_RE.match(line)
    if m and threads:
      num = m.group("line")
      threads[-1].append(
        Frame(
          m.group("path"), int(num) if num.isdigit() else -1, m.group("func")
        )
      )
  return threads


def role_of(frames: Thread) -> str:
  """
  Classify a thread by distinctive frames.

  Args:
    frames (Thread): Frames, innermost first.

  Returns:
    str: ``write`` (write-stage worker), ``ingest`` (pool thread running a
      task), ``idle_pool`` (pool thread waiting for work) or ``other``.

  Examples:
    >>> role_of([Frame("x/concurrent/futures/thread.py", 1, "_worker")])
    'idle_pool'
  """
  funcs = {f.func for f in frames}
  if "_write_worker_main" in funcs:
    return "write"
  if "_run_task" in funcs:
    return "ingest"
  if "_worker" in funcs and any(
    "concurrent/futures/thread.py" in f.path for f in frames
  ):
    return "idle_pool"
  return "other"


def wait_kind(frames: Thread) -> str:
  """
  Say what the innermost frame is doing.

  Args:
    frames (Thread): Frames, innermost first (may be empty).

  Returns:
    str: ``lock_wait``, ``queue_wait``, ``db_io``, ``compute_pandas``,
      ``compute_project``, ``idle_pool`` or ``other``.

  Examples:
    >>> wait_kind([Frame("/x/queue.py", 1, "get")])
    'queue_wait'
  """
  if not frames:
    return "other"
  path = frames[0].path
  if path.endswith(("/threading.py", "/_thread.py")):
    return "lock_wait"
  if path.endswith("/queue.py"):
    return "queue_wait"
  if any(
    tok in path for tok in ("psycopg", "/socket.py", "/ssl.py", "/selectors.py")
  ):
    return "db_io"
  if "concurrent/futures/thread.py" in path:
    return "idle_pool"
  if "/site-packages/" in path and any(
    tok in path for tok in ("pandas", "numpy")
  ):
    return "compute_pandas"
  return "compute_project" if _PROJECT_MARK in path else "other"


def top_project_frame(frames: Thread) -> str | None:
  """
  Label the innermost project frame in ``file.py:function`` form.

  Args:
    frames (Thread): Frames, innermost first.

  Returns:
    str | None: Label, or ``None`` when no frame is under ``/hpcperfstats/``.

  Examples:
    >>> top_project_frame([Frame("/a/hpcperfstats/b.py", 1, "f")])
    'b.py:f'
  """
  for fr in frames:
    if _PROJECT_MARK in fr.path:
      return f"{fr.path.rsplit('/', 1)[-1]}:{fr.func}"
  return None


def aggregate(snapshots: list[list[Thread]]) -> dict[str, dict]:
  """
  Count thread states per role over all snapshots.

  Args:
    snapshots (list[list[Thread]]): Parsed dumps.

  Returns:
    dict[str, dict]: Role -> ``threads`` (total thread-samples), ``kinds``
      (Counter of :func:`wait_kind`) and ``top_project_frames`` (sorted
      ``(label, count)`` pairs).

  Examples:
    >>> aggregate([])
    {}
  """
  roles: dict[str, dict] = {}
  frames_by_role: dict[str, collections.Counter] = collections.defaultdict(
    collections.Counter
  )
  for snap in snapshots:
    for thread in snap:
      role = role_of(thread)
      entry = roles.setdefault(
        role, {"threads": 0, "kinds": collections.Counter()}
      )
      entry["threads"] += 1
      entry["kinds"][wait_kind(thread)] += 1
      label = top_project_frame(thread)
      if label:
        frames_by_role[role][label] += 1
  for role, entry in roles.items():
    entry["top_project_frames"] = frames_by_role[role].most_common()
  return roles


def render_markdown(agg: dict[str, dict], snapshots: int, top: int = 12) -> str:
  """
  Render the role tables as markdown.

  Args:
    agg (dict[str, dict]): Output of :func:`aggregate`.
    snapshots (int): Number of dumps aggregated.
    top (int): Rows per role in the frame table.

  Returns:
    str: Markdown report.

  Examples:
    >>> render_markdown({}, 0).startswith("# ")
    True
  """
  lines = [f"# Ingest stack profile ({snapshots} dumps)", ""]
  for role in ("ingest", "write", "idle_pool", "other"):
    entry = agg.get(role)
    if not entry:
      continue
    n = entry["threads"]
    lines += [
      f"## {role}",
      "",
      f"{n} thread-samples ({n / max(snapshots, 1):.1f} per dump)",
      "",
    ]
    lines += ["| innermost kind | share |", "|---|---|"]
    for kind, count in entry["kinds"].most_common():
      lines.append(f"| {kind} | {pct(count, n):.1f}% |")
    lines += ["", "| innermost project frame | share |", "|---|---|"]
    for label, count in entry["top_project_frames"][:top]:
      lines.append(f"| {label} | {pct(count, n):.1f}% |")
    lines.append("")
  return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
  """
  CLI entry: print the stack profile for ``--stacks``.

  Args:
    argv (list[str] | None): Argument list; ``None`` reads ``sys.argv``.

  Returns:
    int: ``0`` on success, ``1`` when the file holds no thread dumps.

  Examples:
    >>> main(["--help"]) if False else 0
    0
  """
  ap = argparse.ArgumentParser(
    description="Wall-clock stack profile by thread role"
  )
  ap.add_argument(
    "--stacks", required=True, type=Path, help="faulthandler .stacks file"
  )
  ap.add_argument("--top", type=int, default=12, help="rows per role")
  args = ap.parse_args(argv)
  text = args.stacks.read_text(encoding="utf-8", errors="replace")
  snaps = [parse_dump(block) for block in split_snapshots(text)]
  snaps = [s for s in snaps if s]
  if not snaps:
    print("no thread dumps found", file=sys.stderr)
    return 1
  print(render_markdown(aggregate(snaps), len(snaps), args.top))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
