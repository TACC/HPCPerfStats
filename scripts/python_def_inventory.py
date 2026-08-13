#!/usr/bin/env python3
"""
AST inventory and Google-docstring / signature-hint coverage gate.

Attributes:
  AI_SLOP_AS_ANN_RE: ``AI_SLOP_AS_ANN_RE``.
  AI_SLOP_CLASS_TYPE_RE: ``AI_SLOP_CLASS_TYPE_RE``.
  AI_SLOP_SUMMARY_RE: ``AI_SLOP_SUMMARY_RE``.
  ARG_LINE_RE: ``ARG_LINE_RE``.
  BARE_RAISE_SENTINEL: ``BARE_RAISE_SENTINEL``.
  EXCLUSION_RULES: ``EXCLUSION_RULES``.
  FORBIDDEN_DOC_PHRASES: ``FORBIDDEN_DOC_PHRASES``.
  PLACEHOLDER_EXAMPLE_LINE_RE: ``PLACEHOLDER_EXAMPLE_LINE_RE``.
  RAISE_ENTRY_RE: ``RAISE_ENTRY_RE``.
  SECTION_HEADER_RE: ``SECTION_HEADER_RE``.
  SKIP_DIR_NAMES: ``SKIP_DIR_NAMES``.
  TRIVIAL_DUNDERS: ``TRIVIAL_DUNDERS``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

SKIP_DIR_NAMES = frozenset(
  {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "egg-info",
    "hpcperfstats.egg-info",
    "hpcperfstats_tools.egg-info",
    "test_runs",
  }
)

# Path segment / name rules → excluded_reason (first match wins).
EXCLUSION_RULES: tuple[tuple[str, str], ...] = (
  ("monitor", "monitor_read_only"),
  ("migrations", "django_migrations"),
  ("cursor-hooks", "cursor_hooks"),
  ("tests", "test_tree"),
  ("docs", "docs_scratch"),
)

TRIVIAL_DUNDERS = frozenset(
  {
    "__str__",
    "__repr__",
    "__init__",
    "__hash__",
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__bool__",
    "__len__",
    "__iter__",
    "__next__",
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__call__",
    "__getitem__",
    "__setitem__",
    "__delitem__",
    "__contains__",
  }
)

SECTION_HEADER_RE = re.compile(
  r"^(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|Raise|"
  r"Note|Notes|Example|Examples|Attributes|Warning|Warnings):\s*$",
  re.MULTILINE,
)
ARG_LINE_RE = re.compile(
  r"^\s{2,}(\*{0,2}[A-Za-z_][A-Za-z0-9_]*)\s*"
  r"(?:\([^)]*\))?\s*:\s*\S",
  re.MULTILINE,
)
RAISE_ENTRY_RE = re.compile(
  r"^\s{2,}([A-Za-z_][\w\.]*)\s*:\s*\S",
  re.MULTILINE,
)
BARE_RAISE_SENTINEL = "Exception"


@dataclass
class DefRecord:
  """
  One inventoried function or method.
  
  Attributes:
    doc_ok: Attribute.
    excluded: Attribute.
    excluded_reason: Attribute.
    has_docstring: Attribute.
    is_trivial_dunder: Attribute.
    issues: Attribute.
    kind: Attribute.
    lineno: Attribute.
    name: Attribute.
    path: Attribute.
    qualname: Attribute.
    repo: Attribute.
    sig_annotated: Attribute.
  """

  repo: str
  path: str
  qualname: str
  name: str
  lineno: int
  kind: str
  excluded: bool = False
  excluded_reason: str = ""
  has_docstring: bool = False
  doc_ok: bool = False
  sig_annotated: bool = False
  is_trivial_dunder: bool = False
  issues: list[str] = field(default_factory=list)

  @property
  def ok(self) -> bool:
    """
    Return True when excluded or fully compliant.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DefRecord().ok()  # doctest: +SKIP
    """
    if self.excluded:
      return True
    return self.doc_ok and self.sig_annotated and not self.issues


def _repo_label(root: Path) -> str:
  """
  Map a scan root to a stable inventory repo label.
  
  Args:
    root (Path): String for root.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _repo_label("x")  # doctest: +SKIP
  """
  name = root.resolve().name
  if name == "hpcperfstats-tools":
    return "hpcperfstats-tools"
  return "HPCPerfStats"


def exclusion_reason_for_path(rel_path: Path) -> str | None:
  """
  Return an exclusion reason for a relative path, if any.
  
  Args:
    rel_path (Path): String for rel path.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> exclusion_reason_for_path("x")  # doctest: +SKIP
  """
  parts = rel_path.parts
  name = rel_path.name
  if name == "conftest.py" or name.startswith("test_"):
    return "test_module"
  for part in parts:
    if part.endswith(".egg-info"):
      return "packaging_metadata"
    for needle, reason in EXCLUSION_RULES:
      if part == needle:
        return reason
  return None


def iter_python_files(root: Path) -> Iterator[Path]:
  """
  Yield ``*.py`` files under root, skipping cache/vendor dirs.
  
  Args:
    root (Path): String for root.
  
  Yields:
    Iterator[Path]: Iterator[Path] produced by this call.
  
  Examples:
    >>> iter_python_files("x")  # doctest: +SKIP
  """
  root = root.resolve()
  for path in sorted(root.rglob("*.py")):
    if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
      continue
    yield path


def _ann_present(node: ast.AST | None) -> bool:
  """
  Return True when an annotation AST node is present.
  
  Args:
    node (ast.AST | None): One of ``ast.AST``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _ann_present(None)  # doctest: +SKIP
  """
  return node is not None


def documentable_params(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
  """
  List parameter names that must appear in Args (excludes self/cls).
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> documentable_params(None)  # doctest: +SKIP
  """
  names: list[str] = []
  args = fn.args
  posonly = list(args.posonlyargs)
  normal = list(args.args)
  combined = posonly + normal
  for i, arg in enumerate(combined):
    if i == 0 and arg.arg in ("self", "cls"):
      continue
    names.append(arg.arg)
  if args.vararg is not None:
    names.append(f"*{args.vararg.arg}")
  for arg in args.kwonlyargs:
    names.append(arg.arg)
  if args.kwarg is not None:
    names.append(f"**{args.kwarg.arg}")
  return names


def signature_annotation_issues(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
  """
  Return issues for missing parameter or return annotations.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> signature_annotation_issues(None)  # doctest: +SKIP
  """
  issues: list[str] = []
  args = fn.args
  posonly = list(args.posonlyargs)
  normal = list(args.args)
  combined = posonly + normal
  for i, arg in enumerate(combined):
    if i == 0 and arg.arg in ("self", "cls"):
      continue
    if not _ann_present(arg.annotation):
      issues.append(f"missing_param_annotation:{arg.arg}")
  if args.vararg is not None and not _ann_present(args.vararg.annotation):
    issues.append(f"missing_param_annotation:*{args.vararg.arg}")
  for arg in args.kwonlyargs:
    if not _ann_present(arg.annotation):
      issues.append(f"missing_param_annotation:{arg.arg}")
  if args.kwarg is not None and not _ann_present(args.kwarg.annotation):
    issues.append(f"missing_param_annotation:**{args.kwarg.arg}")
  if not _ann_present(fn.returns):
    issues.append("missing_return_annotation")
  return issues


def _section_body(doc: str, header: str) -> str:
  """
  Extract the body text of a Google docstring section.
  
  Args:
    doc (str): String for doc.
    header (str): String for header.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _section_body("x", "x")  # doctest: +SKIP
  """
  aliases = {
    "Args": ("Args", "Arguments", "Parameters"),
    "Returns": ("Returns", "Return"),
    "Yields": ("Yields", "Yield"),
    "Raises": ("Raises", "Raise"),
    "Examples": ("Examples", "Example"),
    "Attributes": ("Attributes",),
  }
  names = aliases.get(header, (header,))
  match = None
  for alias in names:
    pattern = re.compile(rf"^[ \t]*{re.escape(alias)}:\s*$", re.MULTILINE)
    match = pattern.search(doc)
    if match:
      break
  if not match:
    return ""
  rest = doc[match.end() :]
  next_hdr = re.compile(
    r"^[ \t]*(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|"
    r"Raise|Note|Notes|Example|Examples|Attributes|Warning|Warnings):\s*$",
    re.MULTILINE,
  ).search(rest)
  if next_hdr:
    return rest[: next_hdr.start()]
  return rest


def _is_generator(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
  """
  Return True if the function body yields values.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_generator(None)  # doctest: +SKIP
  """

  class _YieldFinder(ast.NodeVisitor):
    """
    Internal helper to handle YieldFinder.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Attributes:
      _depth: ``_depth``.
      found: ``found``.
    """
    def __init__(self) -> None:
      """
      Initialize a new instance.
      
      Returns:
        None
      
      Examples:
        >>> _YieldFinder()  # doctest: +SKIP
      """
      self.found = False
      self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      """
      Visit a ``FunctionDef`` node while walking the AST.
      
      Args:
        node (ast.FunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> _YieldFinder().visit_FunctionDef(None)  # doctest: +SKIP
      """
      if self._depth == 0:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
      # Skip nested function bodies.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      """
      Visit a ``AsyncFunctionDef`` node while walking the AST.
      
      Args:
        node (ast.AsyncFunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> _YieldFinder().visit_AsyncFunctionDef(None)  # doctest: +SKIP
      """
      if self._depth == 0:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Yield(self, node: ast.Yield) -> None:
      """
      Visit a ``Yield`` node while walking the AST.
      
      Args:
        node (ast.Yield): Node.
      
      Returns:
        None
      
      Examples:
        >>> _YieldFinder().visit_Yield(None)  # doctest: +SKIP
      """
      if self._depth == 1:
        self.found = True

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
      """
      Visit a ``YieldFrom`` node while walking the AST.
      
      Args:
        node (ast.YieldFrom): Node.
      
      Returns:
        None
      
      Examples:
        >>> _YieldFinder().visit_YieldFrom(None)  # doctest: +SKIP
      """
      if self._depth == 1:
        self.found = True

  finder = _YieldFinder()
  finder.visit(fn)
  return finder.found


def _ast_name_of(node: ast.AST | None) -> str | None:
  """
  Return a dotted/simple name for an exception type expression.
  
  Args:
    node (ast.AST | None): One of ``ast.AST``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> _ast_name_of(None)  # doctest: +SKIP
  """
  if node is None:
    return None
  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    base = _ast_name_of(node.value)
    if base:
      return f"{base}.{node.attr}"
    return node.attr
  return None


def collect_raised_exception_names(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
  """
  Collect distinct exception type names raised directly in ``fn``.
  
  Nested function bodies are skipped. Bare ``raise`` (no exc) maps to
  ``Exception`` when no named types are found.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> collect_raised_exception_names(None)  # doctest: +SKIP
  """

  class _RaiseFinder(ast.NodeVisitor):
    """
    Internal helper to handle RaiseFinder.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Attributes:
      _depth: ``_depth``.
      bare: ``bare``.
      named: ``named``.
    """
    def __init__(self) -> None:
      """
      Initialize a new instance.
      
      Returns:
        None
      
      Examples:
        >>> _RaiseFinder()  # doctest: +SKIP
      """
      self.named: list[str] = []
      self.bare = False
      self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      """
      Visit a ``FunctionDef`` node while walking the AST.
      
      Args:
        node (ast.FunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> _RaiseFinder().visit_FunctionDef(None)  # doctest: +SKIP
      """
      if self._depth == 0:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      """
      Visit a ``AsyncFunctionDef`` node while walking the AST.
      
      Args:
        node (ast.AsyncFunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> _RaiseFinder().visit_AsyncFunctionDef(None)  # doctest: +SKIP
      """
      if self._depth == 0:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Raise(self, node: ast.Raise) -> None:
      """
      Visit a ``Raise`` node while walking the AST.
      
      Args:
        node (ast.Raise): Node.
      
      Returns:
        None
      
      Examples:
        >>> _RaiseFinder().visit_Raise(None)  # doctest: +SKIP
      """
      if self._depth != 1:
        return
      if node.exc is None:
        self.bare = True
        return
      target = node.exc
      if isinstance(target, ast.Call):
        target = target.func
      name = _ast_name_of(target)
      if name:
        self.named.append(name)
      else:
        self.named.append(BARE_RAISE_SENTINEL)

  finder = _RaiseFinder()
  finder.visit(fn)
  names = sorted(set(finder.named))
  if not names and finder.bare:
    return [BARE_RAISE_SENTINEL]
  if finder.bare and BARE_RAISE_SENTINEL not in names:
    names = sorted(set(names) | {BARE_RAISE_SENTINEL})
  return names


def _self_attr_name(target: ast.AST) -> str | None:
  """
  Return ``attr`` when ``target`` is ``self.attr`` / ``cls.attr``.
  
  Args:
    target (ast.AST): Target.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> _self_attr_name(None)  # doctest: +SKIP
  """
  if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
    if target.value.id in ("self", "cls"):
      return target.attr
  return None


def collect_class_instance_attrs(cls: ast.ClassDef) -> list[str]:
  """
  Harvest instance attributes from ``__init__``/``__new__`` and class fields.
  
  Includes private names (leading ``_``). Class-body ``AnnAssign`` fields
  (dataclass-style) are included.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> collect_class_instance_attrs()  # doctest: +SKIP
  """
  names: set[str] = set()
  for item in cls.body:
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
      names.add(item.target.id)
    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
      continue
    if item.name not in ("__init__", "__new__"):
      continue

    class _AttrWalk(ast.NodeVisitor):
      """
      Internal helper to handle AttrWalk.
      
      Subclasses ``NodeVisitor``, extending that type with this class's fields
      and behavior.
      
      Subclasses ``NodeVisitor``, extending that type with this class's fields
      and behavior.
      """
      def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        Visit a ``FunctionDef`` node while walking the AST.
        
        Args:
          node (ast.FunctionDef): Node.
        
        Returns:
          None
        
        Examples:
          >>> _AttrWalk().visit_FunctionDef(None)  # doctest: +SKIP
        """
        if node is item:
          self.generic_visit(node)

      def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """
        Visit a ``AsyncFunctionDef`` node while walking the AST.
        
        Args:
          node (ast.AsyncFunctionDef): Node.
        
        Returns:
          None
        
        Examples:
          >>> _AttrWalk().visit_AsyncFunctionDef(None)  # doctest: +SKIP
        """
        if node is item:
          self.generic_visit(node)

      def visit_Assign(self, node: ast.Assign) -> None:
        """
        Visit a ``Assign`` node while walking the AST.
        
        Args:
          node (ast.Assign): Node.
        
        Returns:
          None
        
        Examples:
          >>> _AttrWalk().visit_Assign(None)  # doctest: +SKIP
        """
        for t in node.targets:
          attr = _self_attr_name(t)
          if attr:
            names.add(attr)

      def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """
        Visit a ``AnnAssign`` node while walking the AST.
        
        Args:
          node (ast.AnnAssign): Node.
        
        Returns:
          None
        
        Examples:
          >>> _AttrWalk().visit_AnnAssign(None)  # doctest: +SKIP
        """
        attr = _self_attr_name(node.target)
        if attr:
          names.add(attr)

    _AttrWalk().visit(item)
  return sorted(names)


def collect_module_level_attrs(tree: ast.Module) -> list[str]:
  """
  Harvest module-level assigned names (including private).
  
  Skips ``__dunder__`` module attributes other than keeping ordinary ``_x``.
  
  Args:
    tree (ast.Module): Tree.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> collect_module_level_attrs(None)  # doctest: +SKIP
  """
  names: set[str] = set()
  for node in tree.body:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
      targets.extend(node.targets)
    elif isinstance(node, ast.AnnAssign):
      targets.append(node.target)
    else:
      continue
    for t in targets:
      if isinstance(t, ast.Name):
        if t.id.startswith("__") and t.id.endswith("__"):
          continue
        names.add(t.id)
      elif isinstance(t, ast.Tuple):
        for elt in t.elts:
          if isinstance(elt, ast.Name):
            if elt.id.startswith("__") and elt.id.endswith("__"):
              continue
            names.add(elt.id)
  return sorted(names)


def docstring_summary_line(doc: str) -> str:
  """Return the first prose summary line of a Google docstring.

  Args:
    doc (str): Full docstring text.

  Returns:
    str: First non-empty line before a Google section header, else empty.

  Examples:
    >>> docstring_summary_line("Identity.\\n\\nArgs:\\n  x (int): X.")
    'Identity.'
  """
  for raw in doc.strip().splitlines():
    line = raw.strip()
    if not line:
      continue
    if SECTION_HEADER_RE.match(line):
      break
    return line
  return ""


def summary_echoes_name(summary: str, name: str) -> bool:
  """Return True when ``summary`` is only the identifier (or ``name dunder``).

  Args:
    summary (str): First docstring summary line.
    name (str): Function or class identifier.

  Returns:
    bool: True when the summary echoes ``name`` without behavior prose.

  Examples:
    >>> summary_echoes_name("Command.", "Command")
    True
    >>> summary_echoes_name("Print session counts.", "Command")
    False
  """
  text = summary.strip().rstrip(".").strip()
  if not text:
    return True
  low = text.lower()
  nlow = name.lower()
  if low == nlow:
    return True
  if low == f"{nlow} dunder":
    return True
  return False


def summary_is_ai_slop(summary: str, *, kind: str = "function") -> bool:
  """
  Return True when the summary is a known upgrade-helper template.
  
  Args:
    summary (str): String for summary.
    kind (str): String for kind.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> summary_is_ai_slop("x", "x")  # doctest: +SKIP
  """
  text = summary.strip()
  if not text:
    return True
  if AI_SLOP_SUMMARY_RE.match(text):
    return True
  if kind == "class" and AI_SLOP_CLASS_TYPE_RE.match(text):
    return True
  return False


def examples_are_placeholder_skip(examples_body: str) -> bool:
  """
  Return True when Examples only has empty ``name(...)`` / ``...`` prompts.
  
  Real usage (concrete args, comments plus a non-ellipsis call, etc.) passes.
  ``# doctest: +SKIP`` is allowed when the ``>>>`` call itself is substantive.
  
  Args:
    examples_body (str): String for examples body.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> examples_are_placeholder_skip("x")  # doctest: +SKIP
  """
  prompts = [
    ln for ln in examples_body.splitlines() if ln.lstrip().startswith(">>>")
  ]
  if not prompts:
    return False
  return all(PLACEHOLDER_EXAMPLE_LINE_RE.match(ln) for ln in prompts)


def attributes_section_issues(doc: str, required: Sequence[str]) -> list[str]:
  """Require an Attributes section listing every ``required`` name.

  Args:
    doc (str): Full docstring text.
    required (Sequence[str]): Attribute names that must appear.

  Returns:
    list[str]: Issue codes for missing Attributes section or entries.

  Examples:
    >>> attributes_section_issues("X.\\n\\nAttributes:\\n  a: A.", ["a"])
    []
  """
  if not required:
    return []
  body = _section_body(doc, "Attributes")
  if not body.strip():
    return ["missing_attributes_section"]
  found = {m.group(1) for m in ARG_LINE_RE.finditer(body)}
  issues: list[str] = []
  for name in required:
    if name not in found:
      issues.append(f"missing_attributes_entry:{name}")
  return issues


def module_surface_issues(tree: ast.Module) -> list[str]:
  """Validate module docstring summary + Attributes for module-level vars.

  Args:
    tree (ast.Module): Parsed module AST.

  Returns:
    list[str]: Issue codes for the module surface.

  Examples:
    >>> module_surface_issues(__import__("ast").parse("x = 1\\n"))
    ['missing_module_docstring']
  """
  doc = ast.get_docstring(tree)
  if not doc or not doc.strip():
    return ["missing_module_docstring"]
  return attributes_section_issues(doc, collect_module_level_attrs(tree))


def class_surface_issues(cls: ast.ClassDef) -> list[str]:
  """Validate class docstring summary + Attributes for instance attrs.

  Args:
    cls (ast.ClassDef): Class AST node.

  Returns:
    list[str]: Issue codes for the class surface.

  Examples:
    >>> class_surface_issues(__import__("ast").parse("class C:\\n  pass\\n").body[0])
    ['missing_class_docstring']
  """
  doc = ast.get_docstring(cls)
  if not doc or not doc.strip():
    return ["missing_class_docstring"]
  issues: list[str] = []
  summary = docstring_summary_line(doc)
  if summary_echoes_name(summary, cls.name):
    issues.append("summary_echoes_name")
  elif summary_is_ai_slop(summary, kind="class"):
    issues.append("summary_ai_slop")
  low = doc.lower()
  for phrase in FORBIDDEN_DOC_PHRASES:
    if phrase in low:
      issues.append(f"forbidden_doc_phrase:{phrase}")
  if AI_SLOP_AS_ANN_RE.search(doc):
    issues.append("args_as_ann_slop")
  issues.extend(attributes_section_issues(doc, collect_class_instance_attrs(cls)))
  return issues


def _is_property(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
  """
  Return True when ``fn`` is decorated with ``@property``.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_property(None)  # doctest: +SKIP
  """
  for dec in fn.decorator_list:
    if isinstance(dec, ast.Name) and dec.id == "property":
      return True
    if isinstance(dec, ast.Attribute) and dec.attr == "property":
      return True
  return False


FORBIDDEN_DOC_PHRASES: tuple[str, ...] = (
  "see callers",
  "see return sites",
  "see call sites",
  "polymorphic/legacy",
  "call site",
  "call-site",
  "call sites",
  "by call site",
  "vary by call site",
  "depending on call-site",
  "keyword-arg polymorphism",
  "variadic positional polymorphism",
  # Upgrade-helper AI slop (name-echo templates / polymorphism boilerplate).
  "internal helper for ",
  "compute or apply ",
  "value polymorphism",
  "open polymorphism",
  "config polymorphism",
  "filesystem path polymorphism",
  "path-sequence polymorphism",
  "cli argv polymorphism",
  "discriminator polymorphism",
  "time polymorphism",
  "lock-handle polymorphism",
  "task-payload polymorphism",
  "job polymorphism",
  "callable polymorphism",
  "ast/tree node polymorphism",
  "handle polymorphism",
  "exception polymorphism",
  "result of ``",
)

# Summaries that only restate the identifier with a fixed template prefix.
AI_SLOP_SUMMARY_RE = re.compile(
  r"^(Internal helper for |Compute or apply ).+",
  re.IGNORECASE,
)
# Class stubs like ``ArchiveJobSlot type.`` (not prose that happens to end in "type").
AI_SLOP_CLASS_TYPE_RE = re.compile(
  r"^[A-Za-z_][\w.]*\s+type\.?$",
  re.IGNORECASE,
)
# Args template: "df as ``Optional[pd.DataFrame]``."
AI_SLOP_AS_ANN_RE = re.compile(
  r"^[A-Za-z_][\w ]* as ``[^`]+``\.\s*$",
  re.MULTILINE,
)

# >>> name(...)  # doctest: +SKIP  or  >>> ...  # doctest: +SKIP
PLACEHOLDER_EXAMPLE_LINE_RE = re.compile(
  r"^\s*>>>\s*(?:\w+\(\.\.\.\)|\.\.\.)\s*(?:#\s*doctest:\s*\+SKIP)?\s*$"
)


def line_length_for_repo(repo: str) -> int:
  """
  Ruff line-length for a repo label (80 HPCPerfStats, 88 tools).
  
  Args:
    repo (str): String for repo.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> line_length_for_repo("x")  # doctest: +SKIP
  """
  if "tools" in repo.lower():
    return 88
  return 80


def signature_line_length_issues(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  source_lines: Sequence[str],
  *,
  line_length: int,
) -> list[str]:
  """
  Flag ``def`` header physical lines longer than ``line_length``.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
    source_lines (Sequence[str]): Sequence for source lines.
    line_length (int): Integer value for line length.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> signature_line_length_issues(None, [], 0)  # doctest: +SKIP
  """
  if not fn.body:
    return []
  # Signature occupies lines from ``def`` through the line before the body.
  start = fn.lineno
  end = fn.body[0].lineno - 1
  issues: list[str] = []
  for lineno in range(start, end + 1):
    if lineno < 1 or lineno > len(source_lines):
      continue
    line = source_lines[lineno - 1]
    if len(line) > line_length:
      issues.append(
        f"signature_line_too_long:{lineno}:{len(line)}>{line_length}"
      )
  return issues


def docstring_issues(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  *,
  trivial_dunder: bool,
) -> list[str]:
  """Validate Google-style Args/Returns/Examples quality for a def.

  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): Function AST node.
    trivial_dunder (bool): True when ``fn.name`` is a trivial dunder.

  Returns:
    list[str]: Issue codes for docstring gaps or boilerplate.

  Examples:
    >>> docstring_issues(
    ...     __import__("ast").parse("def f():\\n  pass\\n").body[0],
    ...     trivial_dunder=False,
    ... )
    ['missing_docstring']
  """
  doc = ast.get_docstring(fn)
  issues: list[str] = []
  if not doc or not doc.strip():
    return ["missing_docstring"]

  summary = docstring_summary_line(doc)
  if summary_echoes_name(summary, fn.name):
    issues.append("summary_echoes_name")
  elif summary_is_ai_slop(summary, kind="function"):
    issues.append("summary_ai_slop")

  low = doc.lower()
  for phrase in FORBIDDEN_DOC_PHRASES:
    if phrase in low:
      issues.append(f"forbidden_doc_phrase:{phrase}")
  if AI_SLOP_AS_ANN_RE.search(doc):
    issues.append("args_as_ann_slop")

  params = documentable_params(fn)
  generator = _is_generator(fn)

  if params:
    args_body = _section_body(doc, "Args")
    if not args_body.strip():
      issues.append("missing_args_section")
    else:
      found = {m.group(1) for m in ARG_LINE_RE.finditer(args_body)}
      for name in params:
        # Allow documenting without stars: args vs *args.
        bare = name.lstrip("*")
        if (
          name not in found
          and bare not in found
          and f"*{bare}" not in found
          and f"**{bare}" not in found
        ):
          issues.append(f"missing_args_entry:{name}")
  elif trivial_dunder:
    # No documentable params — Args section optional.
    pass

  returns_body = _section_body(doc, "Returns")
  yields_body = _section_body(doc, "Yields")
  if generator:
    if not yields_body.strip() and not returns_body.strip():
      issues.append("missing_yields_or_returns_section")
  else:
    if not returns_body.strip():
      issues.append("missing_returns_section")
    else:
      first = returns_body.strip().splitlines()[0].strip()
      if not first:
        issues.append("empty_returns_section")
      elif not re.match(
        r"^(None|`?None`?|[A-Za-z_][\w\.\[\], \|]*|:)",
        first,
      ) and ":" not in first and first.lower() != "none":
        if not re.search(r"\bNone\b", first) and ":" not in first:
          issues.append("returns_missing_type_or_none")

  raised = collect_raised_exception_names(fn)
  if raised:
    raises_body = _section_body(doc, "Raises")
    if not raises_body.strip():
      issues.append("missing_raises_section")
    else:
      found = {m.group(1) for m in RAISE_ENTRY_RE.finditer(raises_body)}
      for exc in raised:
        if exc not in found and exc.split(".")[-1] not in found:
          issues.append(f"missing_raises_entry:{exc}")

  examples_body = _section_body(doc, "Examples")
  if not examples_body.strip():
    issues.append("missing_examples_section")
  elif ">>>" not in examples_body:
    issues.append("examples_missing_doctest_prompt")
  elif examples_are_placeholder_skip(examples_body):
    issues.append("examples_placeholder_skip")

  return issues


def _kind_for_stack(stack: list[str]) -> str:
  """
  Classify a def as function, method, or nested from the visit stack.
  
  Args:
    stack (list[str]): Sequence for stack.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _kind_for_stack([])  # doctest: +SKIP
  """
  if "F" in stack:
    return "nested"
  if "C" in stack:
    return "method"
  return "function"


class _InventoryVisitor(ast.NodeVisitor):
  """
  Collect DefRecord rows for one module.
  
  Attributes:
    excluded: Attribute.
    excluded_reason: Attribute.
    line_length: Attribute.
    qual_stack: Attribute.
    records: Attribute.
    rel_path: Attribute.
    repo: Attribute.
    source_lines: Attribute.
    stack: Attribute.
  """

  def __init__(
    self,
    *,
    repo: str,
    rel_path: str,
    excluded: bool,
    excluded_reason: str,
    source_lines: Sequence[str] | None = None,
    line_length: int = 80,
  ) -> None:
    """
    Initialize visitor state for one file.
    
    Args:
      repo (str): String for repo.
      rel_path (str): String for rel path.
      excluded (bool): Boolean flag for excluded.
      excluded_reason (str): String for excluded reason.
      source_lines (Sequence[str] | None): One of ``Sequence[str]``, ``None``.
      line_length (int): Integer value for line length.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor("x", "x", True, "x", None, 0)  # doctest: +SKIP
    """
    self.repo = repo
    self.rel_path = rel_path
    self.excluded = excluded
    self.excluded_reason = excluded_reason
    self.source_lines = list(source_lines or [])
    self.line_length = line_length
    self.stack: list[str] = []
    self.qual_stack: list[str] = []
    self.records: list[DefRecord] = []

  def visit_ClassDef(self, node: ast.ClassDef) -> None:
    """
    Record class surface docs, then visit methods.
    
    Args:
      node (ast.ClassDef): Node.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor().visit_ClassDef(None)  # doctest: +SKIP
    """
    self._record_class(node)
    self.stack.append("C")
    self.qual_stack.append(node.name)
    self.generic_visit(node)
    self.qual_stack.pop()
    self.stack.pop()

  def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
    """
    Record and recurse into a synchronous function.
    
    Args:
      node (ast.FunctionDef): Node.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor().visit_FunctionDef(None)  # doctest: +SKIP
    """
    self._record(node)
    self.stack.append("F")
    self.qual_stack.append(node.name)
    self.generic_visit(node)
    self.qual_stack.pop()
    self.stack.pop()

  def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
    """
    Record and recurse into an async function.
    
    Args:
      node (ast.AsyncFunctionDef): Node.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor().visit_AsyncFunctionDef(None)  # doctest: +SKIP
    """
    self._record(node)
    self.stack.append("F")
    self.qual_stack.append(node.name)
    self.generic_visit(node)
    self.qual_stack.pop()
    self.stack.pop()

  def _record_class(self, node: ast.ClassDef) -> None:
    """
    Append a DefRecord for a class docstring / Attributes surface.
    
    Args:
      node (ast.ClassDef): Node.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor()._record_class(None)  # doctest: +SKIP
    """
    qual = ".".join([*self.qual_stack, node.name])
    issues: list[str] = []
    has_doc = bool(ast.get_docstring(node))
    doc_ok = True
    if not self.excluded:
      issues.extend(class_surface_issues(node))
      doc_ok = not issues
    self.records.append(
      DefRecord(
        repo=self.repo,
        path=self.rel_path,
        qualname=qual,
        name=node.name,
        lineno=node.lineno,
        kind="class",
        excluded=self.excluded,
        excluded_reason=self.excluded_reason if self.excluded else "",
        has_docstring=has_doc,
        doc_ok=doc_ok if not self.excluded else True,
        sig_annotated=True,
        is_trivial_dunder=False,
        issues=issues,
      )
    )

  def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    """
    Append a DefRecord for ``node``.
    
    Args:
      node (ast.FunctionDef | ast.AsyncFunctionDef): One of
      ``ast.FunctionDef``, ``ast.AsyncFunctionDef``.
    
    Returns:
      None
    
    Examples:
      >>> _InventoryVisitor()._record(None)  # doctest: +SKIP
    """
    qual = ".".join([*self.qual_stack, node.name])
    trivial = node.name in TRIVIAL_DUNDERS
    issues: list[str] = []
    has_doc = bool(ast.get_docstring(node))
    doc_ok = False
    sig_ok = False
    if _is_property(node):
      kind = "property"
    else:
      kind = _kind_for_stack(self.stack)
    if not self.excluded:
      sig_issues = signature_annotation_issues(node)
      if self.source_lines:
        sig_issues.extend(
          signature_line_length_issues(
            node,
            self.source_lines,
            line_length=self.line_length,
          )
        )
      doc_issues = docstring_issues(node, trivial_dunder=trivial)
      issues.extend(sig_issues)
      issues.extend(doc_issues)
      sig_ok = not sig_issues
      doc_ok = not doc_issues
    self.records.append(
      DefRecord(
        repo=self.repo,
        path=self.rel_path,
        qualname=qual,
        name=node.name,
        lineno=node.lineno,
        kind=kind,
        excluded=self.excluded,
        excluded_reason=self.excluded_reason if self.excluded else "",
        has_docstring=has_doc,
        doc_ok=doc_ok if not self.excluded else True,
        sig_annotated=sig_ok if not self.excluded else True,
        is_trivial_dunder=trivial,
        issues=issues,
      )
    )


def inventory_file(path: Path, *, root: Path, repo: str) -> list[DefRecord]:
  """
  Inventory all defs in one Python file.
  
  Args:
    path (Path): String for path.
    root (Path): String for root.
    repo (str): String for repo.
  
  Returns:
    list[DefRecord]: list[DefRecord] produced by this call.
  
  Examples:
    >>> inventory_file("x", "x", "x")  # doctest: +SKIP
  """
  rel = path.relative_to(root)
  reason = exclusion_reason_for_path(rel)
  excluded = reason is not None
  try:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
  except (OSError, SyntaxError, UnicodeDecodeError):
    return [
      DefRecord(
        repo=repo,
        path=str(rel).replace("\\", "/"),
        qualname="<parse_error>",
        name="<parse_error>",
        lineno=1,
        kind="function",
        excluded=excluded,
        excluded_reason=reason or "parse_error",
        issues=["parse_error"] if not excluded else [],
      )
    ]
  visitor = _InventoryVisitor(
    repo=repo,
    rel_path=str(rel).replace("\\", "/"),
    excluded=excluded,
    excluded_reason=reason or "",
    source_lines=source.splitlines(),
    line_length=line_length_for_repo(repo),
  )
  # Module surface record (summary + Attributes for module-level vars).
  mod_issues: list[str] = []
  has_mod_doc = bool(ast.get_docstring(tree))
  if not excluded:
    mod_issues = module_surface_issues(tree)
  visitor.records.append(
    DefRecord(
      repo=repo,
      path=str(rel).replace("\\", "/"),
      qualname="<module>",
      name="<module>",
      lineno=1,
      kind="module",
      excluded=excluded,
      excluded_reason=reason or "",
      has_docstring=has_mod_doc,
      doc_ok=(not mod_issues) if not excluded else True,
      sig_annotated=True,
      is_trivial_dunder=False,
      issues=mod_issues if not excluded else [],
    )
  )
  visitor.visit(tree)
  return visitor.records


def build_inventory(
  roots: Sequence[Path],
  *,
  path_filter: str | None = None,
) -> list[DefRecord]:
  """
  Build a full inventory across one or more roots.
  
  Args:
    roots (Sequence[Path]): String for roots.
    path_filter (str | None): One of ``str``, ``None``.
  
  Returns:
    list[DefRecord]: list[DefRecord] produced by this call.
  
  Examples:
    >>> build_inventory("x", None)  # doctest: +SKIP
  """
  records: list[DefRecord] = []
  for root in roots:
    root = root.resolve()
    repo = _repo_label(root)
    for path in iter_python_files(root):
      records.extend(inventory_file(path, root=root, repo=repo))
  if path_filter:
    records = [r for r in records if path_filter in r.path]
  return records


def inventory_summary(records: Sequence[DefRecord]) -> dict[str, Any]:
  """
  Compute summary counters for an inventory.
  
  Args:
    records (Sequence[DefRecord]): Sequence for records.
  
  Returns:
    dict[str, Any]: dict[str, Any] produced by this call.
  
  Examples:
    >>> inventory_summary([])  # doctest: +SKIP
  """
  in_scope = [r for r in records if not r.excluded]
  failed = [r for r in in_scope if not r.ok]
  by_reason: dict[str, int] = {}
  for r in records:
    if r.excluded:
      by_reason[r.excluded_reason or "unknown"] = (
        by_reason.get(r.excluded_reason or "unknown", 0) + 1
      )
  return {
    "total_defs": len(records),
    "in_scope": len(in_scope),
    "excluded": len(records) - len(in_scope),
    "in_scope_ok": len(in_scope) - len(failed),
    "in_scope_failed": len(failed),
    "excluded_by_reason": by_reason,
  }


def records_to_jsonable(
  records: Sequence[DefRecord],
  *,
  roots: Sequence[str],
  in_scope_only: bool = True,
) -> dict[str, Any]:
  """
  Serialize inventory records to a JSON-compatible document.
  
  Args:
    records (Sequence[DefRecord]): Sequence for records.
    roots (Sequence[str]): Sequence for roots.
    in_scope_only (bool): Boolean flag for in scope only.
  
  Returns:
    dict[str, Any]: dict[str, Any] produced by this call.
  
  Examples:
    >>> records_to_jsonable([], [], True)  # doctest: +SKIP
  """
  summary = inventory_summary(records)
  defs = [asdict(r) for r in records if not r.excluded] if in_scope_only else [
    asdict(r) for r in records
  ]
  doc: dict[str, Any] = {
    "version": 1,
    "roots": list(roots),
    "summary": summary,
    "defs": defs,
  }
  if in_scope_only:
    doc["excluded_summary"] = summary.get("excluded_by_reason", {})
  return doc


def default_roots(workspace_root: Path | None = None) -> list[Path]:
  """
  Resolve default scan roots for this workspace layout.
  
  Args:
    workspace_root (Path | None): One of ``Path``, ``None``.
  
  Returns:
    list[Path]: list[Path] produced by this call.
  
  Examples:
    >>> default_roots(None)  # doctest: +SKIP
  """
  if workspace_root is None:
    # scripts/ → HPCPerfStats git checkout → workspace root
    checkout = Path(__file__).resolve().parent.parent
    workspace_root = checkout.parent
  else:
    workspace_root = workspace_root.resolve()
    checkout = workspace_root / "HPCPerfStats"
    if not checkout.is_dir():
      checkout = workspace_root
  roots: list[Path] = []
  if checkout.is_dir():
    roots.append(checkout)
  # Prefer in-tree monorepo client; fall back to legacy workspace-root sibling.
  tools_in_checkout = checkout / "hpcperfstats-tools"
  tools_sibling = workspace_root / "hpcperfstats-tools"
  if tools_in_checkout.is_dir():
    roots.append(tools_in_checkout)
  elif tools_sibling.is_dir() and tools_sibling.resolve() != tools_in_checkout.resolve():
    roots.append(tools_sibling)
  return roots


def failing_records(records: Sequence[DefRecord]) -> list[DefRecord]:
  """
  Filter to in-scope non-compliant records.
  
  Args:
    records (Sequence[DefRecord]): Sequence for records.
  
  Returns:
    list[DefRecord]: list[DefRecord] produced by this call.
  
  Examples:
    >>> failing_records([])  # doctest: +SKIP
  """
  return [r for r in records if not r.excluded and not r.ok]


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry: write inventory JSON and/or fail on coverage gaps.
  
  Args:
    argv (Sequence[str] | None): One of ``Sequence[str]``, ``None``.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> main(None)  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(
    description="Inventory Python defs and gate Google docstring + hints.",
  )
  parser.add_argument(
    "--root",
    action="append",
    type=Path,
    default=None,
    help="Scan root (repeatable). Default: HPCPerfStats + in-tree hpcperfstats-tools.",
  )
  parser.add_argument(
    "--workspace-root",
    type=Path,
    default=None,
    help="Workspace root used when --root is omitted.",
  )
  parser.add_argument(
    "--write",
    type=Path,
    default=None,
    help="Write full inventory JSON to this path.",
  )
  parser.add_argument(
    "--check",
    action="store_true",
    help="Exit 1 if any in-scope def fails the docstring/hint contract.",
  )
  parser.add_argument(
    "--path-filter",
    default=None,
    help="Only consider records whose path contains this substring.",
  )
  parser.add_argument(
    "--max-fail-print",
    type=int,
    default=40,
    help="Max failing defs to print on --check (default 40).",
  )
  args = parser.parse_args(list(argv) if argv is not None else None)

  roots = list(args.root) if args.root else default_roots(args.workspace_root)
  if not roots:
    print("No scan roots found", file=sys.stderr)
    return 1

  records = build_inventory(roots, path_filter=args.path_filter)
  summary = inventory_summary(records)
  print(json.dumps(summary, indent=2, sort_keys=True))

  if args.write is not None:
    doc = records_to_jsonable(
      records,
      roots=[str(r.resolve()) for r in roots],
    )
    args.write.parent.mkdir(parents=True, exist_ok=True)
    args.write.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {args.write} ({len(records)} defs)", file=sys.stderr)

  if args.check:
    failed = failing_records(records)
    if failed:
      print(f"FAIL: {len(failed)} in-scope def(s) non-compliant", file=sys.stderr)
      for rec in failed[: args.max_fail_print]:
        print(
          f"  {rec.repo}:{rec.path}:{rec.lineno} {rec.qualname} "
          f"{','.join(rec.issues)}",
          file=sys.stderr,
        )
      if len(failed) > args.max_fail_print:
        print(
          f"  … {len(failed) - args.max_fail_print} more",
          file=sys.stderr,
        )
      return 1
    print("OK: all in-scope defs compliant", file=sys.stderr)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
