#!/usr/bin/env python3
"""Fact ledger for doc consolidation: extract candidate facts, check coverage.

When pages are merged or moved (docs/reference/documentation-style.md,
section "Consolidation method"), every unique technical fact must reach a
destination page or be dropped with evidence. This script builds the ledger
and reconciles it.

Subcommands:

- ``extract <FILE>...`` prints a Markdown ledger table of candidate facts in
  the source files. Save it under ``test_runs/docs-consolidation/``.
- ``check <LEDGER> <DESTINATION>...`` exits 1 and lists the failing rows when
  the ledger is not reconciled with the destination pages; exits 0 and prints
  ``DOC_FACT_COVERAGE_OK <ROWS> rows`` otherwise.

Ledger format (one GitHub table; other lines in the file are ignored, so a
hand list of prose caveats can sit above or below it, or be added as rows with
kind ``caveat``):

```text
| Token | Kind | Source | Status | Evidence |
| --- | --- | --- | --- | --- |
| `[PIPELINE] archive_zstd_threads` | ini-key | docs/x.md:12 | keep | |
| `--dry-run` | flag | docs/x.md:40 | drop | removed in run.py argparse |
```

- Token: the fact as a code span (a token holding a backtick uses a double
  backtick span); ``|`` inside a token is written ``\\|``.
- Kind: ``ini-key``, ``flag``, ``env``, ``version``, ``quantity``, ``path``,
  ``command`` (first line of each shell-fence command), ``code`` (any other
  code span), or ``caveat`` for hand-added prose facts.
- Source: ``file:line`` of the first occurrence; extract emits each token once.
- Status (blank from extract; the editor fills it in):

  - ``keep``: the token must appear verbatim in a destination page.
  - ``fix``: replace the Token cell with the corrected value and cite the pin
    in Evidence; the corrected token must appear in a destination page.
  - ``drop``: Evidence must say why the fact is stale (the code or commit
    that removed it).
  - ``ask``: unresolved owner question; check fails until it is answered.

- Evidence: free text; required for ``fix`` and ``drop``.

check fails on any row with a blank or unknown status, so every candidate is
classified before the old page is deleted.

Attributes:
  HEADER: First two lines of an emitted ledger table.
  STATUSES: Status values check accepts.
  EVIDENCE_REQUIRED: Statuses whose rows need an Evidence note.
  COVERED_STATUSES: Statuses whose token must appear in a destination.
  SHELL_LANGS: Fence info strings whose lines are emitted as commands.
  _FENCE_RE: Opening or closing code fence with its language tag.
  _CODE_SPAN_RE: Inline code span.
  _URL_RE: URL, removed before path and number matching.
  _INI_KEY_RE: ``[SECTION] key`` in prose or a code span.
  _INI_SECTION_RE: ``[SECTION]`` header line inside an ini fence.
  _INI_ASSIGN_RE: ``key = value`` line inside an ini fence.
  _FLAG_RE: Long CLI flag.
  _ENV_RE: UPPER_SNAKE name with at least one underscore.
  _VERSION_RE: Three-part version pin.
  _QUANTITY_RE: Number with a unit.
  _PATH_RE: Repository-style path.
  _SPLIT_CELLS_RE: Table cell separator (unescaped pipe).
  _KIND_ORDER: Extractor order; an earlier kind claims a token first.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

HEADER = "| Token | Kind | Source | Status | Evidence |\n| --- | --- | --- | --- | --- |"
STATUSES = frozenset({"keep", "fix", "drop", "ask"})
EVIDENCE_REQUIRED = frozenset({"fix", "drop"})
COVERED_STATUSES = frozenset({"keep", "fix"})
SHELL_LANGS = frozenset({"bash", "sh", "shell", "console", "zsh"})

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)")
_CODE_SPAN_RE = re.compile(r"(`+)\s?(.+?)\s?\1")
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_INI_KEY_RE = re.compile(
  r"\[([A-Z][A-Z0-9_]*)\](?:`?\**\s+\**`?)([a-z][a-z0-9_]+)"
)
_INI_SECTION_RE = re.compile(r"^\s*\[([A-Za-z][\w.-]*)\]\s*$")
_INI_ASSIGN_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*[=:]")
_FLAG_RE = re.compile(r"(?<![\w-])--[a-z0-9][a-z0-9-]*(?:=\S+)?")
_ENV_RE = re.compile(r"(?<![\w<])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![\w>])")
_VERSION_RE = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?:-[\w.]+)?(?![\w]|\.\d)")
_QUANTITY_RE = re.compile(
  r"(?<![\w.])\d+(?:\.\d+)?\s?"
  r"(?:[KMGTP]i?B|[KMGT]bit|ms|us|µs|ns|s|sec|min|h|GHz|MHz|W|%|"
  r"°C|seconds?|minutes?|hours?|days?|jobs/day)(?![\w/])"
)
_PATH_RE = re.compile(
  r"(?<![\w./~-])(?:~/|\.{1,2}/|/)?(?:[\w.-]+/)+[\w.-]*"
  r"|(?<![\w./-])[\w-]+\.(?:py|sh|md|ini|yaml|yml|toml|json|conf|sql|ts|tsx|js"
  r"|service|txt|tsv|cfg|am|ac)(?![\w])"
)
_SPLIT_CELLS_RE = re.compile(r"(?<!\\)\|")
_KIND_ORDER = ("ini-key", "flag", "version", "quantity", "env", "path")


def _looks_like_path(token: str) -> bool:
  """
  Return whether a slash-bearing match is a file path, not prose.

  Prose such as ``and/or`` matches the raw pattern; a path has an extension,
  a trailing slash, or an explicit root.

  Args:
    token (str): Candidate matched by ``_PATH_RE``.

  Returns:
    bool: ``True`` for tokens kept as ``path`` facts.

  Examples:
    >>> _looks_like_path("docs/TESTING.md"), _looks_like_path("and/or")
    (True, False)
  """
  if "/" not in token:
    return True
  last = token.rsplit("/", 1)[-1]
  return token.endswith("/") or "." in last or token.startswith(("/", "./"))


def _line_tokens(line: str) -> Iterator[tuple[str, str]]:
  """
  Yield ``(token, kind)`` facts found in one line of text.

  Args:
    line (str): Source line (prose or fence body).

  Yields:
    tuple[str, str]: Token and its ledger kind, in ``_KIND_ORDER``.

  Examples:
    >>> list(_line_tokens("Set `[PIPELINE] x_y` with --dry-run"))
    [('[PIPELINE] x_y', 'ini-key'), ('--dry-run', 'flag')]
  """
  text = _URL_RE.sub(" ", line)
  patterns = {
    "ini-key": _INI_KEY_RE,
    "flag": _FLAG_RE,
    "version": _VERSION_RE,
    "quantity": _QUANTITY_RE,
    "env": _ENV_RE,
    "path": _PATH_RE,
  }
  for kind in _KIND_ORDER:
    for match in patterns[kind].finditer(text):
      if kind == "ini-key":
        yield f"[{match.group(1)}] {match.group(2)}", kind
      elif kind != "path" or _looks_like_path(match.group(0)):
        yield match.group(0).rstrip(".,;:"), kind


def extract_facts(path: Path) -> Iterator[tuple[str, str, int]]:
  """
  Yield ``(token, kind, line)`` candidate facts from one Markdown file.

  Prose lines yield code spans plus the token patterns; ``ini`` fences add
  ``[SECTION] key`` for each assignment; shell fences add each command's
  first line as a ``command`` fact. Duplicates are not removed here.

  Args:
    path (Path): Markdown file to scan.

  Yields:
    tuple[str, str, int]: Token, kind and 1-based line number.

  Examples:
    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> f = d / "a.md"
    >>> _ = f.write_text("```ini\\n[RMQ]\\nport = 5672\\n```\\n")
    >>> [t for t, _k, _n in extract_facts(f)]
    ['[RMQ] port']
  """
  fence: str | None = None
  lang = ""
  section = ""
  continued = False
  lines = path.read_text(encoding="utf-8").splitlines()
  for n, line in enumerate(lines, 1):
    match = _FENCE_RE.match(line)
    if fence is None and match:
      fence, lang, section, continued = (
        match.group(1),
        match.group(2),
        "",
        False,
      )
      continue
    if fence is not None:
      if (
        line.strip()
        and not line.strip().strip(fence[0])
        and (line.strip().startswith(fence))
      ):
        fence = None
        continue
      yield from ((t, k, n) for t, k in _line_tokens(line))
      stripped = line.strip()
      if lang == "ini":
        header = _INI_SECTION_RE.match(line)
        assign = _INI_ASSIGN_RE.match(line)
        if header:
          section = header.group(1)
        elif assign and section:
          yield f"[{section}] {assign.group(1)}", "ini-key", n
      elif lang in SHELL_LANGS and stripped and not stripped.startswith("#"):
        if not continued:
          yield stripped.rstrip("\\").rstrip(), "command", n
        continued = stripped.endswith("\\")
      continue
    yield from ((t, k, n) for t, k in _line_tokens(line))
    for span in _CODE_SPAN_RE.finditer(line):
      yield span.group(2).strip(), "code", n


def _code_cell(token: str) -> str:
  """
  Return ``token`` as a table-safe code span.

  Args:
    token (str): Fact text.

  Returns:
    str: Code span with pipes escaped.

  Examples:
    >>> _code_cell("a|b"), _code_cell("x`y")
    ('`a\\\\|b`', '`` x`y ``')
  """
  escaped = token.replace("|", "\\|")
  return f"`` {escaped} ``" if "`" in token else f"`{escaped}`"


def render_ledger(paths: Sequence[Path]) -> str:
  """
  Return the ledger table for ``paths``, one row per distinct token.

  Args:
    paths (Sequence[Path]): Source Markdown files in ledger order.

  Returns:
    str: Markdown table (``HEADER`` plus rows) ending in a newline.

  Examples:
    >>> render_ledger([]).splitlines()[0]
    '| Token | Kind | Source | Status | Evidence |'
  """
  seen: set[str] = set()
  rows = [HEADER]
  for path in paths:
    for token, kind, n in extract_facts(path):
      if not token or token in seen:
        continue
      seen.add(token)
      rows.append(f"| {_code_cell(token)} | {kind} | {path}:{n} |  |  |")
  return "\n".join(rows) + "\n"


def _cell_token(cell: str) -> str:
  """
  Return the fact text held in a ledger Token cell.

  Args:
    cell (str): Raw cell text between pipes.

  Returns:
    str: Token with the code span and pipe escapes removed.

  Examples:
    >>> _cell_token(" `a\\\\|b` "), _cell_token("`` x`y ``"), _cell_token("p")
    ('a|b', 'x`y', 'p')
  """
  cell = cell.strip()
  ticks = len(cell) - len(cell.lstrip("`"))
  if ticks and cell.endswith("`" * ticks):
    cell = cell[ticks:-ticks]
    if ticks > 1:
      cell = cell.strip()
  return cell.replace("\\|", "|")


def parse_ledger(text: str) -> list[tuple[int, str, str, str, str]]:
  """
  Return ``(line, token, kind, status, evidence)`` for each ledger row.

  Header and separator rows and lines that are not 5-cell table rows are
  skipped.

  Args:
    text (str): Ledger file contents.

  Returns:
    list[tuple[int, str, str, str, str]]: Rows with lowercased status.

  Examples:
    >>> parse_ledger(HEADER + "\\n| `x` | code | a.md:1 | Keep | |")
    [(3, 'x', 'code', 'keep', '')]
  """
  rows = []
  for n, line in enumerate(text.splitlines(), 1):
    stripped = line.strip()
    if not stripped.startswith("|"):
      continue
    cells = _SPLIT_CELLS_RE.split(stripped.strip("|"))
    if len(cells) != 5:
      continue
    token, kind, _source, status, evidence = (c.strip() for c in cells)
    if token == "Token" or set(token) <= set("-: "):
      continue
    rows.append((n, _cell_token(token), kind, status.lower(), evidence))
  return rows


def check_ledger(ledger: Path, destinations: Sequence[Path]) -> list[str]:
  """
  Return one error per ledger row that is not reconciled.

  Args:
    ledger (Path): Ledger Markdown file.
    destinations (Sequence[Path]): Pages that must hold the kept facts.

  Returns:
    list[str]: ``ledger:line: reason`` messages; empty when reconciled.

  Examples:
    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> led, dest = d / "l.md", d / "p.md"
    >>> _ = dest.write_text("use `--dry-run`")
    >>> _ = led.write_text(HEADER + "\\n| `--dry-run` | flag | a:1 | keep | |")
    >>> check_ledger(led, [dest])
    []
  """
  corpus = "\n".join(p.read_text(encoding="utf-8") for p in destinations)
  errors = []
  for n, token, _kind, status, evidence in parse_ledger(
    ledger.read_text(encoding="utf-8")
  ):
    where = f"{ledger}:{n}: {token!r}"
    if status not in STATUSES:
      errors.append(
        f"{where}: status {status or '<blank>'!r} not in keep/fix/drop"
      )
    elif status == "ask":
      errors.append(f"{where}: unresolved owner question (status ask)")
    elif status in EVIDENCE_REQUIRED and not evidence:
      errors.append(f"{where}: status {status} needs an Evidence note")
    if status in COVERED_STATUSES and token not in corpus:
      errors.append(f"{where}: {status} token missing from destinations")
  return errors


def main(argv: Sequence[str] | None = None) -> int:
  """
  Run the ``extract`` or ``check`` subcommand.

  Args:
    argv (Sequence[str] | None): CLI args; defaults to ``sys.argv[1:]``.

  Returns:
    int: ``0`` on success, ``1`` when check finds unreconciled rows.

  Examples:
    >>> import contextlib, io, tempfile
    >>> f = Path(tempfile.mkdtemp()) / "a.md"
    >>> _ = f.write_text("Use --x-y")
    >>> buf = io.StringIO()
    >>> with contextlib.redirect_stdout(buf):
    ...   rc = main(["extract", str(f)])
    >>> rc, "| `--x-y` | flag |" in buf.getvalue()
    (0, True)
  """
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = parser.add_subparsers(dest="cmd", required=True)
  ext = sub.add_parser("extract", help="print a ledger for source files")
  ext.add_argument("files", nargs="+", type=Path)
  chk = sub.add_parser("check", help="reconcile a ledger with destinations")
  chk.add_argument("ledger", type=Path)
  chk.add_argument("destinations", nargs="+", type=Path)
  args = parser.parse_args(argv)
  if args.cmd == "extract":
    sys.stdout.write(render_ledger(args.files))
    return 0
  errors = check_ledger(args.ledger, args.destinations)
  if errors:
    print("\n".join(errors))
    return 1
  rows = len(parse_ledger(args.ledger.read_text(encoding="utf-8")))
  print(f"DOC_FACT_COVERAGE_OK {rows} rows")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
