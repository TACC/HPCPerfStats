#!/usr/bin/env python3
"""
Restore pre-upgrade module/class docstring prose lost by force-docs.

Merges the rich summary body from ``git show HEAD:<path>`` into the current
Google-style docstring while keeping required ``Attributes:`` entries (and any
other structured sections already present).

Usage (from HPCPerfStats checkout)::

../.venv/bin/python3 scripts/restore_docstring_prose_from_git.py --apply

Attributes:
  SECTION_START_RE: Attribute.
  _SCRIPTS: Attribute.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
  sys.path.insert(0, str(_SCRIPTS))

from python_def_inventory import (  # noqa: E402
  collect_class_instance_attrs,
  collect_module_level_attrs,
  exclusion_reason_for_path,
)

SECTION_START_RE = re.compile(
  r"^(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|Raise|"
  r"Note|Notes|Example|Examples|Attributes|Warning|Warnings|Todo):\s*$"
)


def _split_prose_and_sections(doc: str) -> tuple[str, str]:
  """
  Split a docstring into leading prose and trailing structured sections.
  
  Args:
    doc (str): String for doc.
  
  Returns:
    tuple[str, str]: tuple[str, str] produced by this call.
  
  Examples:
    >>> _split_prose_and_sections("x")  # doctest: +SKIP
  """
  if not doc or not doc.strip():
    return "", ""
  lines = doc.splitlines()
  prose: list[str] = []
  idx = 0
  while idx < len(lines):
    if SECTION_START_RE.match(lines[idx].strip()):
      break
    prose.append(lines[idx])
    idx += 1
  while prose and not prose[0].strip():
    prose.pop(0)
  while prose and not prose[-1].strip():
    prose.pop()
  sections = "\n".join(lines[idx:]).strip("\n")
  return "\n".join(prose).strip("\n"), sections


def _attributes_block(names: Sequence[str], *, existing_sections: str) -> str:
  """
  Build an Attributes section, preferring existing entries when present.
  
  Args:
    names (Sequence[str]): Sequence for names.
    existing_sections (str): String for existing sections.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _attributes_block([], "x")  # doctest: +SKIP
  """
  if not names:
    return ""
  existing_body = ""
  m = re.search(
    r"^[ \t]*Attributes:\s*$",
    existing_sections,
    re.MULTILINE,
  )
  if m:
    rest = existing_sections[m.end() :]
    nxt = re.search(
      r"^[ \t]*(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|"
      r"Raise|Note|Notes|Example|Examples|Warning|Warnings|Todo):\s*$",
      rest,
      re.MULTILINE,
    )
    existing_body = rest[: nxt.start()] if nxt else rest
  found: dict[str, str] = {}
  for line in existing_body.splitlines():
    mm = re.match(
      r"^\s{2,}(\*{0,2}[A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$",
      line,
    )
    if mm:
      found[mm.group(1)] = mm.group(2).strip()
  out = ["Attributes:"]
  for name in names:
    desc = found.get(name, "").strip()
    # Drop force-docs scaffolding and broken wrapped leftovers.
    if (
      not desc
      or desc.startswith("Module-level")
      or desc.startswith("Instance attribute")
      or desc == f"``{name}``."
    ):
      desc = f"``{name}``."
    out.append(f"  {name}: {desc}")
  return "\n".join(out)


def _prefer_prose(old_prose: str, new_prose: str) -> str:
  """
  Choose the richer leading prose block.
  
  Args:
    old_prose (str): String for old prose.
    new_prose (str): String for new prose.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _prefer_prose("x", "x")  # doctest: +SKIP
  """
  if len(old_prose.strip()) >= len(new_prose.strip()) + 40:
    return old_prose.strip("\n")
  if not old_prose.strip():
    return new_prose.strip("\n")
  if not new_prose.strip():
    return old_prose.strip("\n")
  # Prefer old when it has more paragraphs even if only slightly longer.
  if old_prose.count("\n\n") > new_prose.count("\n\n") and len(old_prose) > len(
    new_prose
  ):
    return old_prose.strip("\n")
  return new_prose.strip("\n")


def _format_doc_clean(prose: str, *sections: str, indent: str) -> str:
  """
  Format docstring with clean blank lines.
  
  Args:
    prose (str): String for prose.
    *sections (str): Extra positional values for ``sections``; element types
    match the helper's documented protocol.
    indent (str): String for indent.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _format_doc_clean("x", "x")  # doctest: +SKIP
  """
  chunks: list[str] = []
  if prose.strip():
    chunks.append(prose.strip("\n"))
  for sec in sections:
    if sec and sec.strip():
      chunks.append(sec.strip("\n"))
  body = "\n\n".join(chunks).strip("\n")
  out_lines = [f'{indent}"""']
  if body:
    for line in body.splitlines():
      out_lines.append(f"{indent}{line}" if line.strip() else f"{indent}")
  out_lines.append(f'{indent}"""')
  return "\n".join(out_lines) + "\n"


def _replace_docstring_node(
  source: str,
  node: ast.AST,
  new_doc_block: str,
) -> str:
  """
  Replace an existing leading docstring under ``node``.
  
  Args:
    source (str): String for source.
    node (ast.AST): Node.
    new_doc_block (str): String for new doc block.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _replace_docstring_node("x", None, "x")  # doctest: +SKIP
  """
  body = getattr(node, "body", None)
  if not body:
    return source
  first = body[0]
  has_doc = (
    isinstance(first, ast.Expr)
    and isinstance(getattr(first, "value", None), ast.Constant)
    and isinstance(first.value.value, str)
  )
  lines = source.splitlines(keepends=True)
  new_lines = new_doc_block.splitlines(keepends=True)
  if new_lines and not new_lines[-1].endswith("\n"):
    new_lines[-1] += "\n"
  if has_doc:
    end = first.end_lineno or first.lineno
    lines[first.lineno - 1 : end] = new_lines
    return "".join(lines)
  # Insert before first body statement.
  insert_idx = first.lineno - 1
  lines[insert_idx:insert_idx] = new_lines
  return "".join(lines)


def restore_file(path: Path, *, root: Path, old_source: str) -> tuple[str, int]:
  """
  Restore module/class prose for one file.
  
  Args:
    path (Path): String for path.
    root (Path): String for root.
    old_source (str): String for old source.
  
  Returns:
    tuple[str, int]: tuple[str, int] produced by this call.
  
  Examples:
    >>> restore_file("x", "x", "x")  # doctest: +SKIP
  """
  rel = str(path.relative_to(root)).replace("\\", "/")
  if exclusion_reason_for_path(Path(rel)) is not None:
    return path.read_text(encoding="utf-8"), 0

  new_source = path.read_text(encoding="utf-8")
  try:
    old_tree = ast.parse(old_source)
    ast.parse(new_source)
  except SyntaxError:
    return new_source, 0

  restored = 0
  updated = new_source

  old_by_name: dict[str, list[ast.ClassDef]] = {}
  for n in ast.walk(old_tree):
    if isinstance(n, ast.ClassDef):
      old_by_name.setdefault(n.name, []).append(n)

  while True:
    try:
      cur_tree = ast.parse(updated)
    except SyntaxError:
      return updated, restored
    classes = sorted(
      (n for n in ast.walk(cur_tree) if isinstance(n, ast.ClassDef)),
      key=lambda n: n.lineno,
      reverse=True,
    )
    progressed = False
    for cls in classes:
      candidates = old_by_name.get(cls.name) or []
      old_cls = candidates[0] if candidates else None
      if old_cls is None:
        continue
      old_prose, _ = _split_prose_and_sections(ast.get_docstring(old_cls) or "")
      new_doc = ast.get_docstring(cls) or ""
      new_prose, new_sections = _split_prose_and_sections(new_doc)
      prose = _prefer_prose(old_prose, new_prose)
      if prose == new_prose.strip("\n") and len(old_prose) < len(new_prose) + 40:
        continue
      if not old_prose.strip() or len(old_prose) < 40:
        continue
      if len(old_prose) <= len(new_prose) + 20:
        continue
      attrs = collect_class_instance_attrs(cls)
      attr_sec = _attributes_block(attrs, existing_sections=new_sections)
      # Preserve non-Attributes sections from new docstring if any.
      other = new_sections
      if re.search(r"^[ \t]*Attributes:\s*$", other, re.MULTILINE):
        other = re.sub(
          r"^[ \t]*Attributes:\s*\n(?:^[ \t]+.+\n?)*",
          "",
          other,
          count=1,
          flags=re.MULTILINE,
        ).strip("\n")
      cls_line = updated.splitlines()[cls.lineno - 1]
      base = re.match(r"^[ \t]*", cls_line).group(0)
      body_indent = base + ("\t" if "\t" in base else "  ")
      if cls.body:
        first_line = updated.splitlines()[cls.body[0].lineno - 1]
        m = re.match(r"^[ \t]*", first_line)
        if m and len(m.group(0)) > len(base):
          body_indent = m.group(0)
      block = _format_doc_clean(prose, other, attr_sec, indent=body_indent)
      replaced = _replace_docstring_node(updated, cls, block)
      if replaced != updated:
        updated = replaced
        restored += 1
        progressed = True
        break
    if not progressed:
      break

  # Module docstring.
  try:
    cur_tree = ast.parse(updated)
  except SyntaxError:
    return updated, restored
  old_prose, _ = _split_prose_and_sections(ast.get_docstring(old_tree) or "")
  new_doc = ast.get_docstring(cur_tree) or ""
  new_prose, new_sections = _split_prose_and_sections(new_doc)
  if old_prose.strip() and len(old_prose) > len(new_prose) + 40:
    prose = old_prose.strip("\n")
    attrs = collect_module_level_attrs(cur_tree)
    attr_sec = _attributes_block(attrs, existing_sections=new_sections)
    other = new_sections
    if re.search(r"^[ \t]*Attributes:\s*$", other, re.MULTILINE):
      other = re.sub(
        r"^[ \t]*Attributes:\s*\n(?:^[ \t]+.+\n?)*",
        "",
        other,
        count=1,
        flags=re.MULTILINE,
      ).strip("\n")
    block = _format_doc_clean(prose, other, attr_sec, indent="")
    replaced = _replace_docstring_node(updated, cur_tree, block)
    if replaced != updated:
      updated = replaced
      restored += 1

  return updated, restored


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: restore docstring prose from git HEAD into the working tree.
  
  Args:
    argv (Sequence[str] | None): One of ``Sequence[str]``, ``None``.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> main(None)  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--root",
    type=Path,
    default=Path(__file__).resolve().parents[1],
    help="Git checkout root (default: HPCPerfStats).",
  )
  parser.add_argument("--apply", action="store_true", help="Write changes.")
  parser.add_argument(
    "--path-filter",
    default="",
    help="Only process paths containing this substring.",
  )
  args = parser.parse_args(list(argv) if argv is not None else None)
  root = args.root.resolve()
  changed = subprocess.check_output(
    ["git", "diff", "--name-only", "HEAD", "--", "*.py"],
    cwd=root,
    text=True,
  ).splitlines()
  skip_prefixes = (
    "hpcperfstats/tests/",
    "cursor-hooks/",
    "scripts/python_def_",
    "scripts/restore_docstring_prose_from_git.py",
  )
  total = 0
  files_touched = 0
  for rel in changed:
    if any(rel.startswith(p) for p in skip_prefixes):
      continue
    if args.path_filter and args.path_filter not in rel:
      continue
    path = root / rel
    if not path.is_file():
      continue
    try:
      old = subprocess.check_output(
        ["git", "show", f"HEAD:{rel}"],
        cwd=root,
        text=True,
      )
    except subprocess.CalledProcessError:
      continue
    updated, n = restore_file(path, root=root, old_source=old)
    if n and updated != path.read_text(encoding="utf-8"):
      files_touched += 1
      total += n
      print(f"{rel}: restored {n} docstring(s)")
      if args.apply:
        path.write_text(updated, encoding="utf-8")
  print(f"{'applied' if args.apply else 'would restore'}: {total} docs in {files_touched} files")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
