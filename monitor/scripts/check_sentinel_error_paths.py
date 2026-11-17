#!/usr/bin/env python3
"""Lint collector C sources for failed reads that hide the failure instead of a value sentinel.

A collector that cannot read a counter must mark it with stats_set_missing* (it is emitted as
``-<n>``), never store 0 (indistinguishable from a real 0) or skip it silently. This flags, in
collector files (``get_current_stats`` / ``stats_set`` / ``stats_inc``), an ``if`` whose condition
tests a read-like call for failure (directly, or a variable the previous statement assigned from
one) and whose body

- stores 0 with ``stats_set(..., 0)``, or
- only logs / cleans up and then does a bare ``continue;`` / ``return;`` / ``goto``.

Intentional cases go in ``check_sentinel_error_paths.allowlist`` as ``<file>: <condition>`` (the
condition text with whitespace collapsed), each with a ``#`` reason. Exit 1 on any finding or a
stale allowlist entry, 0 when clean.

Usage: check_sentinel_error_paths.py [--allowlist PATH] <src dir or .c file>...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ALLOWLIST = Path(__file__).resolve().with_name("check_sentinel_error_paths.allowlist")

_READ_CALL = re.compile(
    r"\b(pread|read|fread|fopen|fdopen|open|openat|opendir|scandir|file_fopen_read|"
    r"path_file_fopen_read|path_read\w*|pscanf|fgets|getline|sscanf|fscanf|strtoull|strtoul|"
    r"ioctl|statvfs|statfs|stat|lstat|setmntent|perf_event_open|perfmon_\w+|dcgm\w+|nvml\w+|"
    r"xpum\w+|lustre_fopen_obd_named|sys_iter_\w+)\s*\("
)
_FAILURE_TEST = re.compile(r"(<\s*0|<=\s*0|==\s*-1|==\s*NULL|!=\s*\d|!\s*\w|==\s*0\b)")
_SET_ZERO = re.compile(r"\bstats_set\s*\([^;]*,\s*0(ULL|UL|U|LL|L)?\s*\)\s*;")
_SKIP = re.compile(r"^\s*(continue|return|goto\s+\w+)\s*$")
# Statements that may precede the skip without handling the failure: logging and cleanup.
_CLEANUP = re.compile(
    r"^\s*(ERROR|TRACE|WARN|monitor_log_\w+|fclose|free|close|closedir|endmntent|"
    r"path_open_record_failure_once|\(void\)\s*\w+)\s*\("
)
_COLLECTOR = re.compile(r"\b(get_current_stats|stats_set|stats_inc)\s*\(")


def _strip_comments(text: str) -> str:
    """Blank out comments and string literals, keeping offsets and newlines."""

    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"', blank, text, flags=re.S)


def _balanced(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket matching text[start] (an ``open_ch``), or -1."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


_VAR_TEST = re.compile(r"^\s*(?:!\s*(\w+)|(\w+)\s*(?:==\s*NULL|<\s*0|<=\s*0|==\s*-1|!=\s*\d+))\s*$")


def _tests_failed_read(code: str, offset: int, cond: str) -> bool:
    """True when ``cond`` tests a read call, or tests a variable the previous statement assigned
    from one (``f = fopen(...); if (f == NULL)``)."""
    if _READ_CALL.search(cond) and _FAILURE_TEST.search(cond):
        return True
    m = _VAR_TEST.match(cond)
    if m is None:
        return False
    var = m.group(1) or m.group(2)
    prev_end = code.rfind(";", 0, offset)
    prev_start = max(code.rfind(";", 0, prev_end), code.rfind("{", 0, prev_end), code.rfind("}", 0, prev_end))
    prev = code[prev_start + 1 : prev_end] if prev_end > 0 else ""
    if code[prev_end + 1 : offset].strip():
        return False
    # Plain assignment or a declaration with an initializer (`FILE *fp = fopen(...)`).
    assigns = re.match(rf"\s*(?:[\w\s*]*[\s*])?{re.escape(var)}\s*=\s*[^=]", prev)
    return assigns is not None and bool(_READ_CALL.search(prev))


def _if_statements(code: str):
    """Yield (offset, condition span, body) for each ``if`` in comment-stripped C source."""
    for m in re.finditer(r"\bif\s*\(", code):
        cond_end = _balanced(code, m.end() - 1, "(", ")")
        if cond_end < 0:
            continue
        cond = (m.end(), cond_end - 1)
        rest = cond_end
        while rest < len(code) and code[rest].isspace():
            rest += 1
        if rest < len(code) and code[rest] == "{":
            body_end = _balanced(code, rest, "{", "}")
            body = code[rest + 1 : body_end - 1] if body_end > 0 else ""
        else:
            semi = code.find(";", rest)
            body = code[rest : semi + 1] if semi >= 0 else ""
        yield m.start(), cond, body


def _silent_skip(body: str) -> bool:
    """True when the body only logs / cleans up, then continues, returns (void) or jumps out."""
    stmts = [x for x in body.strip("{} \n\t").split(";") if x.strip()]
    return (
        bool(stmts)
        and _SKIP.match(stmts[-1]) is not None
        and all(_CLEANUP.match(x) for x in stmts[:-1])
    )


def _normalize(cond: str) -> str:
    return " ".join(cond.split())


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """(line, normalized condition, reason) for each finding in one C file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    code = _strip_comments(text)
    if not _COLLECTOR.search(code):
        return []
    out = []
    for offset, (start, end), body in _if_statements(code):
        if not _tests_failed_read(code, offset, code[start:end]):
            continue
        if "stats_set_missing" in body or "stats_type_set_missing_all" in body:
            continue
        if _SET_ZERO.search(body):
            reason = "stores 0 after a failed read (use stats_set_missing)"
        elif _silent_skip(body):
            reason = "skips a failed read silently (mark the keys with a sentinel)"
        else:
            continue
        out.append((code.count("\n", 0, offset) + 1, _normalize(text[start:end]), reason))
    return out


def load_allowlist(path: Path) -> dict[tuple[str, str], str]:
    """(file basename, normalized condition) -> reason."""
    out: dict[tuple[str, str], str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        name, sep, cond = entry.partition(":")
        if not sep or not reason.strip():
            raise SystemExit(f"check_sentinel_error_paths: bad allowlist line (needs a # reason): {raw!r}")
        out[(name.strip(), _normalize(cond))] = reason.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    args = parser.parse_args(argv)

    files: list[Path] = []
    for p in args.paths:
        files.extend(sorted(p.glob("*.c")) if p.is_dir() else [p])
    allow = load_allowlist(args.allowlist)
    used: set[tuple[str, str]] = set()
    findings = 0
    for f in files:
        for line, cond, reason in scan_file(f):
            key = (f.name, cond)
            if key in allow:
                used.add(key)
                continue
            findings += 1
            print(f"{f}:{line}: if ({cond}): {reason}")
    scanned = {f.name for f in files}
    stale = [k for k in allow if k[0] in scanned and k not in used]
    for name, cond in stale:
        print(f"{args.allowlist}: stale entry {name}: {cond}")
    if findings or stale:
        print(f"check_sentinel_error_paths: {findings} finding(s), {len(stale)} stale allowlist entr(y/ies)")
        return 1
    print(f"check_sentinel_error_paths: OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
