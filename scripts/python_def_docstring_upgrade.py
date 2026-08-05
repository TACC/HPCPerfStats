#!/usr/bin/env python3
"""
Upgrade in-scope Python defs to Google docstrings + signature annotations.

Attributes:
  TRIVIAL_DUNDER_SUMMARIES: ``TRIVIAL_DUNDER_SUMMARIES``.
  _SCRIPTS: ``_SCRIPTS``.
  _VERB_LEADERS: ``_VERB_LEADERS``.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import textwrap
from pathlib import Path
from typing import Sequence

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
  sys.path.insert(0, str(_SCRIPTS))

from python_def_inventory import (  # noqa: E402
  TRIVIAL_DUNDERS,
  _is_generator,
  class_surface_issues,
  collect_class_instance_attrs,
  collect_module_level_attrs,
  collect_raised_exception_names,
  default_roots,
  documentable_params,
  docstring_issues,
  exclusion_reason_for_path,
  module_surface_issues,
  signature_annotation_issues,
)


def _expr_source(source: str, node: ast.AST | None) -> str | None:
  """
  Return the source slice for an AST expression, if available.
  
  Args:
    source (str): String for source.
    node (ast.AST | None): One of ``ast.AST``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> _expr_source("x", None)  # doctest: +SKIP
  """
  if node is None or not hasattr(node, "lineno"):
    return None
  lines = source.splitlines(keepends=True)
  start_l = node.lineno - 1
  end_l = (getattr(node, "end_lineno", None) or node.lineno) - 1
  start_c = node.col_offset
  end_c = getattr(node, "end_col_offset", None)
  if start_l == end_l:
    return lines[start_l][start_c:end_c]
  parts = [lines[start_l][start_c:]]
  for i in range(start_l + 1, end_l):
    parts.append(lines[i])
  parts.append(lines[end_l][:end_c])
  return "".join(parts)


def infer_type_from_default(default: ast.AST | None) -> str | None:
  """
  Infer a type annotation string from a default value AST.
  
  Args:
    default (ast.AST | None): One of ``ast.AST``, ``None``.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> infer_type_from_default(None)  # doctest: +SKIP
  """
  if default is None:
    return None
  if isinstance(default, ast.Constant):
    val = default.value
    if val is None:
      return "Any | None"
    if isinstance(val, bool):
      return "bool"
    if isinstance(val, int):
      return "int"
    if isinstance(val, float):
      return "float"
    if isinstance(val, str):
      return "str"
    if isinstance(val, bytes):
      return "bytes"
  if isinstance(default, (ast.List, ast.ListComp)):
    return "list[Any]"
  if isinstance(default, (ast.Dict, ast.DictComp)):
    return "dict[Any, Any]"
  if isinstance(default, ast.Tuple):
    return "tuple[Any, ...]"
  if isinstance(default, ast.Set):
    return "set[Any]"
  return None


def infer_type_from_name(name: str) -> str:
  """
  Heuristic type for an unannotated parameter name.
  
  Args:
    name (str): String for name.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> infer_type_from_name("x")  # doctest: +SKIP
  """
  bare = name.lstrip("*")
  lower = bare.lower()
  if lower in ("path", "filepath", "filename", "dirname") or lower.endswith(
    ("_path", "_file", "_dir", "_dirname")
  ):
    return "str"
  if lower.startswith(("is_", "has_", "use_", "enable_", "disable_", "skip_", "force_")):
    return "bool"
  if lower.endswith(("_flag", "_enabled", "_disabled")):
    return "bool"
  if lower in (
    "count",
    "size",
    "limit",
    "offset",
    "port",
    "timeout",
    "workers",
    "retries",
    "ttl",
    "nbytes",
    "pid",
    "index",
    "lineno",
    "chunk_size",
  ) or lower.endswith(("_count", "_size", "_limit", "_timeout", "_seconds", "_ms")):
    return "int"
  if lower in ("cfg", "config", "kwargs", "options", "params", "payload", "data"):
    return "Any"
  if name.startswith("*"):
    return "Any"
  return "Any"


def infer_return_type(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
  """
  Infer a return annotation for a function body.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> infer_return_type(None)  # doctest: +SKIP
  """

  class _BodyProbe(ast.NodeVisitor):
    """
    Internal helper to handle BodyProbe.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Attributes:
      _depth: ``_depth``.
      returns_value: ``returns_value``.
      yields: ``yields``.
    """
    def __init__(self) -> None:
      """
      Initialize a new instance.
      
      Returns:
        None
      
      Examples:
        >>> _BodyProbe()  # doctest: +SKIP
      """
      self.yields = False
      self.returns_value = False
      self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      """
      Visit a ``FunctionDef`` node while walking the AST.
      
      Args:
        node (ast.FunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> _BodyProbe().visit_FunctionDef(None)  # doctest: +SKIP
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
        >>> _BodyProbe().visit_AsyncFunctionDef(None)  # doctest: +SKIP
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
        >>> _BodyProbe().visit_Yield(None)  # doctest: +SKIP
      """
      if self._depth == 1:
        self.yields = True

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
      """
      Visit a ``YieldFrom`` node while walking the AST.
      
      Args:
        node (ast.YieldFrom): Node.
      
      Returns:
        None
      
      Examples:
        >>> _BodyProbe().visit_YieldFrom(None)  # doctest: +SKIP
      """
      if self._depth == 1:
        self.yields = True

    def visit_Return(self, node: ast.Return) -> None:
      """
      Visit a ``Return`` node while walking the AST.
      
      Args:
        node (ast.Return): Node.
      
      Returns:
        None
      
      Examples:
        >>> _BodyProbe().visit_Return(None)  # doctest: +SKIP
      """
      if self._depth != 1:
        return
      if node.value is None:
        return
      if isinstance(node.value, ast.Constant) and node.value.value is None:
        return
      self.returns_value = True

  probe = _BodyProbe()
  probe.visit(fn)
  if probe.yields:
    return "Iterator[Any]"
  if probe.returns_value:
    return "Any"
  return "None"


def _param_defaults(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.AST | None]:
  """
  Map parameter names to default AST nodes.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
  
  Returns:
    dict[str, ast.AST | None]: dict[str, ast.AST | None] produced by this
    call.
  
  Examples:
    >>> _param_defaults(None)  # doctest: +SKIP
  """
  args = fn.args
  result: dict[str, ast.AST | None] = {}
  positional = list(args.posonlyargs) + list(args.args)
  defaults = list(args.defaults)
  pad = len(positional) - len(defaults)
  for i, arg in enumerate(positional):
    result[arg.arg] = None if i < pad else defaults[i - pad]
  for arg, default in zip(args.kwonlyargs, args.kw_defaults):
    result[arg.arg] = default
  if args.vararg is not None:
    result[f"*{args.vararg.arg}"] = None
  if args.kwarg is not None:
    result[f"**{args.kwarg.arg}"] = None
  return result


def _param_annotations(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  source: str,
) -> dict[str, str]:
  """
  Resolve annotation text for every documentable parameter.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
    source (str): String for source.
  
  Returns:
    dict[str, str]: dict[str, str] produced by this call.
  
  Examples:
    >>> _param_annotations(None, "x")  # doctest: +SKIP
  """
  defaults = _param_defaults(fn)
  out: dict[str, str] = {}
  args = fn.args
  all_pos = list(args.posonlyargs) + list(args.args)
  skip_first = bool(all_pos and all_pos[0].arg in ("self", "cls"))
  for i, arg in enumerate(all_pos):
    if skip_first and i == 0:
      continue
    existing = _expr_source(source, arg.annotation)
    if existing:
      out[arg.arg] = existing.strip()
      continue
    inferred = infer_type_from_default(defaults.get(arg.arg))
    out[arg.arg] = inferred or infer_type_from_name(arg.arg)
  if args.vararg is not None:
    key = f"*{args.vararg.arg}"
    existing = _expr_source(source, args.vararg.annotation)
    out[key] = (existing or infer_type_from_name(key)).strip()
  for arg in args.kwonlyargs:
    existing = _expr_source(source, arg.annotation)
    if existing:
      out[arg.arg] = existing.strip()
      continue
    inferred = infer_type_from_default(defaults.get(arg.arg))
    out[arg.arg] = inferred or infer_type_from_name(arg.arg)
  if args.kwarg is not None:
    key = f"**{args.kwarg.arg}"
    existing = _expr_source(source, args.kwarg.annotation)
    out[key] = (existing or infer_type_from_name(key)).strip()
  return out

def _split_union_types(ann: str) -> list[str]:
  """
  Split a top-level union annotation on ``|`` (ignores ``|`` inside ``[]``).
  
  Args:
    ann (str): String for ann.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _split_union_types("x")  # doctest: +SKIP
  """
  parts: list[str] = []
  depth = 0
  cur: list[str] = []
  for ch in ann:
    if ch == "[":
      depth += 1
    elif ch == "]":
      depth = max(0, depth - 1)
    elif ch == "|" and depth == 0:
      piece = "".join(cur).strip()
      if piece:
        parts.append(piece)
      cur = []
      continue
    cur.append(ch)
  piece = "".join(cur).strip()
  if piece:
    parts.append(piece)
  return parts or [ann]


def _describe_union(ann: str, *, kind: str) -> str | None:
  """
  Describe a union/optional annotation as explicit polymorphic variants.
  
  Args:
    ann (str): String for ann.
    kind (str): String for kind.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> _describe_union("x", "x")  # doctest: +SKIP
  """
  variants = _split_union_types(ann)
  if len(variants) < 2:
    return None
  pretty = ", ".join(f"``{v}``" for v in variants)
  if kind == "return":
    return f"One of {pretty} depending on inputs/branch."
  return f"One of {pretty}."


def _describe_any_param(bare: str) -> str:
  """Describe an ``Any`` parameter in plain English from its name.

  Args:
    bare (str): Parameter name without stars.

  Returns:
    str: Human Args description (no polymorphism boilerplate).

  Examples:
    >>> _describe_any_param("value")
    'Value to inspect (typically a numeric scalar).'
  """
  lower = bare.lower()
  if lower in ("path", "filepath", "filename", "dirname") or lower.endswith(
    ("_path", "_file", "_dir", "_dirname")
  ):
    return "Filesystem path as a string (or path-like coerced to ``str``)."
  if lower in ("paths", "files", "members", "sealed_paths") or lower.endswith(
    ("_paths", "_files", "_members")
  ):
    return "Iterable of filesystem paths as strings."
  if lower in ("argv", "cli_args", "args_list"):
    return "CLI argument list (``sys.argv``-like)."
  if lower in ("mode", "kind", "phase", "stage"):
    return "Mode or kind token selecting a code path."
  if lower in (
    "startdate",
    "enddate",
    "start",
    "end",
    "when",
    "ts",
    "timestamp",
    "mtime",
  ):
    return "Time value (``datetime``, ISO string, sentinel, or ``None``)."
  if lower in ("lock", "redis_lock", "mutex"):
    return "Lock object used to serialize access."
  if lower in ("tasks", "task", "task_args", "tasks_locked", "chunk", "chunks"):
    return "Task payload for a worker (tuple/list per this helper's protocol)."
  if lower in ("j", "job") or lower.endswith("_job"):
    return "Job record (Django ``job_data`` or job-like mapping)."
  if lower in ("callback", "cb", "handler", "fn", "func", "predicate", "worker") or (
    lower.endswith(("_fn", "_cb", "_handler"))
  ):
    return "Callable invoked by this helper."
  if lower in ("data", "payload", "body", "row", "record", "obj", "value", "item"):
    return "Value to inspect (typically a numeric scalar)."
  if lower.startswith("dram"):
    return "DDR (DRAM) CAS bandwidth value, or something coercible to float."
  if lower.startswith("hbm"):
    return "HBM CAS bandwidth value, or something coercible to float."
  if lower in ("cfg", "config", "options", "params", "kwargs", "settings"):
    return "Settings mapping or object with the keys this helper reads."
  if lower in ("node", "tree", "ast_node"):
    return "AST or tree node to inspect."
  if lower in ("pool", "executor", "client", "session", "conn", "connection"):
    return "Live handle (pool, client, or connection)."
  if lower in ("exc", "err", "error", "exception"):
    return "Exception instance being classified or logged."
  if lower.startswith(("is_", "has_", "use_", "enable_", "skip_", "force_")):
    return "Flag controlling this behavior (usually a ``bool``)."
  words = bare.replace("_", " ")
  return f"{words[0].upper() + words[1:] if words else bare} passed to this helper."


def _describe_param(name: str, ann: str) -> str:
  """Build a plain-English Args-line description.

  Args:
    name (str): Parameter name (may include ``*`` / ``**``).
    ann (str): Annotation source text.

  Returns:
    str: Human Args description.

  Examples:
    >>> _describe_param("df", "Optional[pd.DataFrame]")
    'DataFrame to inspect, or None when absent.'
  """
  bare = name.lstrip("*")
  union = _describe_union(ann, kind="param")
  if union:
    return union
  if name.startswith("**"):
    if bare in ("options", "option"):
      return (
        "Keyword options from the caller or framework (for Django "
        "``BaseCommand.handle``, keys such as ``verbosity``, ``settings``, "
        "``traceback``, ``no_color``, ``force_color``, ``skip_checks``)."
      )
    if bare in ("kwargs", "kw"):
      return (
        "Extra keyword arguments forwarded to the wrapped API; keys and "
        "value types match that callee's signature."
      )
    return (
      f"Extra keyword arguments (``{bare}``); keys are ``str`` and value "
      "types match the wrapped protocol for this helper."
    )
  if name.startswith("*"):
    if bare == "args":
      return (
        "Extra positional arguments; unused unless the callee documents a "
        "specific leftover protocol."
      )
    return (
      f"Extra positional values for ``{bare}``; element types match the "
      "helper's documented protocol."
    )
  if ann == "Any" or ann.startswith("Any"):
    return _describe_any_param(bare)
  if "[Any]" in ann or ann.startswith("list[Any]") or ann.startswith("dict[Any"):
    return f"Container of mixed values (``{ann}``)."
  return _describe_typed_param(bare, ann)


def _describe_typed_param(bare: str, ann: str) -> str:
  """Describe a concretely typed parameter without ``name as Type`` slop.

  Args:
    bare (str): Parameter name.
    ann (str): Annotation source text.

  Returns:
    str: Human Args description.

  Examples:
    >>> _describe_typed_param("n", "int")
    'Integer value for n.'
  """
  flat = re.sub(r"\s+", "", ann)
  optional = flat.startswith("Optional[") or flat.endswith("|None")
  words = bare.replace("_", " ")
  if "DataFrame" in ann:
    return (
      "DataFrame to inspect, or None when absent."
      if optional
      else "DataFrame to inspect."
    )
  if "Series" in ann:
    return (
      "pandas Series to inspect, or None when absent."
      if optional
      else "pandas Series."
    )
  if flat in ("bool",) or flat.startswith("bool"):
    if bare.startswith(("is_", "has_", "use_", "enable_", "skip_", "force_")):
      return f"Whether to enable {words}."
    return f"Boolean flag for {words}."
  if flat in ("int",) or flat.startswith("int|"):
    return f"Integer value for {words}."
  if flat in ("float",) or flat.startswith("float"):
    return f"Floating-point value for {words}."
  if flat in ("str",) or flat.startswith("str|") or "Path" in ann:
    return f"Path or string for {words}." if optional else f"String for {words}."
  if flat.startswith(("list", "List", "tuple", "Tuple", "Sequence", "set", "Set")):
    return f"Sequence for {words}."
  if flat.startswith(("dict", "Dict", "Mapping")):
    return f"Mapping for {words}."
  if optional:
    return f"{words[0].upper() + words[1:] if words else bare}, or None when absent."
  return f"{words[0].upper() + words[1:] if words else bare}."


def _describe_return(fn_name: str, ret: str) -> str:
  """Build a plain-English Returns-line description.

  Args:
    fn_name (str): Function name (unused; kept for call compatibility).
    ret (str): Return annotation source text.

  Returns:
    str: Human Returns description.

  Examples:
    >>> _describe_return("f", "bool")
    'True or False for this check.'
  """
  del fn_name
  union = _describe_union(ret, kind="return")
  if union:
    return union
  if ret == "None":
    return "None"
  if ret == "bool" or ret.startswith("bool"):
    return "True or False for this check."
  if ret == "Any" or ret.startswith("Any") or "[Any]" in ret:
    return "Value produced by this call (type depends on inputs)."
  flat = re.sub(r"\s+", "", ret)
  if flat.startswith("Optional[") or flat.endswith("|None"):
    return f"{ret} — the result, or None when unavailable."
  if "DataFrame" in ret:
    return "Result DataFrame, or None when nothing usable remains."
  return f"{ret} produced by this call."


def _wrap_doc_content_lines(
  content_lines: list[str],
  *,
  indent: str,
  line_length: int,
) -> list[str]:
  """Wrap docstring content so each physical source line fits ``line_length``.

  Args:
    content_lines (list[str]): Docstring content lines without outer quotes.
    indent (str): Indent prefix for measuring available width.
    line_length (int): Max physical line length including indent.

  Returns:
    list[str]: Wrapped content lines (no indent prefix applied yet).

  Examples:
    >>> _wrap_doc_content_lines(["Short."], indent="  ", line_length=80)
    ['Short.']
  """
  width = max(20, line_length - len(indent))
  out: list[str] = []
  for line in content_lines:
    if not line.strip():
      out.append("")
      continue
    stripped = line.lstrip(" ")
    lead = len(line) - len(stripped)
    hang = " " * lead
    if stripped in (
      "Args:",
      "Returns:",
      "Yields:",
      "Raises:",
      "Examples:",
      "Example:",
      "Attributes:",
      "None",
    ) or (
      stripped.endswith(":")
      and stripped[:-1]
      in (
        "Args",
        "Returns",
        "Yields",
        "Raises",
        "Examples",
        "Example",
        "Attributes",
        "Arguments",
        "Parameters",
      )
    ):
      out.append(line)
      continue
    if stripped.startswith(">>>"):
      out.append(line)
      continue
    if re.match(r"^([-*]|\d+\.)\s+", stripped):
      cont = hang + "  "
    else:
      cont = hang
    wrapped = textwrap.wrap(
      stripped,
      width=max(20, width - lead),
      initial_indent=hang,
      subsequent_indent=cont,
      break_long_words=True,
      break_on_hyphens=True,
    )
    out.extend(wrapped if wrapped else [line])
  return out


TRIVIAL_DUNDER_SUMMARIES: dict[str, str] = {
  "__str__": "Return the informal string representation",
  "__repr__": "Return the official string representation",
  "__init__": "Initialize a new instance",
  "__hash__": "Return the hash value for this object",
  "__eq__": "Return True when this object equals ``other``",
  "__ne__": "Return True when this object differs from ``other``",
  "__lt__": "Return True when this object is ordered before ``other``",
  "__le__": "Return True when this object is ordered before or equal to ``other``",
  "__gt__": "Return True when this object is ordered after ``other``",
  "__ge__": "Return True when this object is ordered after or equal to ``other``",
  "__bool__": "Return the truth value of this object",
  "__len__": "Return the number of contained elements",
  "__iter__": "Return an iterator over contained elements",
  "__next__": "Return the next item from the iterator",
  "__enter__": "Enter the runtime context for this object",
  "__exit__": "Exit the runtime context for this object",
  "__aenter__": "Enter the async runtime context for this object",
  "__aexit__": "Exit the async runtime context for this object",
  "__call__": "Invoke this object as a callable",
  "__getitem__": "Return the item at the given key or index",
  "__setitem__": "Assign the item at the given key or index",
  "__delitem__": "Delete the item at the given key or index",
  "__contains__": "Return True when the item is contained",
}


_VERB_LEADERS: dict[str, str] = {
  "get": "return",
  "fetch": "fetch",
  "load": "load",
  "save": "save",
  "write": "write",
  "read": "read",
  "build": "build",
  "create": "create",
  "make": "make",
  "parse": "parse",
  "format": "format",
  "check": "check",
  "ensure": "ensure",
  "validate": "validate",
  "update": "update",
  "delete": "delete",
  "remove": "remove",
  "add": "add",
  "set": "set",
  "run": "run",
  "start": "start",
  "stop": "stop",
  "close": "close",
  "open": "open",
  "combine": "combine",
  "merge": "merge",
  "convert": "convert",
  "compute": "compute",
  "calculate": "calculate",
  "collect": "collect",
  "iter": "iterate over",
  "apply": "apply",
  "render": "render",
  "resolve": "resolve",
  "normalize": "normalize",
  "coerce": "coerce",
  "count": "count",
  "find": "find",
  "list": "list",
  "print": "print",
  "log": "log",
  "wait": "wait for",
  "sleep": "sleep for",
  "seal": "seal",
  "drain": "drain",
  "sync": "sync",
  "ingest": "ingest",
  "archive": "archive",
  "populate": "populate",
  "dispatch": "dispatch",
  "handle": "handle",
  "process": "process",
  "prepare": "prepare",
  "init": "initialize",
  "main": "run the program entrypoint for",
}


def _friendly_words(parts: list[str]) -> str:
  """Expand common abbrev tokens in an identifier word list.

  Args:
    parts (list[str]): Snake_case name parts.

  Returns:
    str: Spaced phrase with friendlier nouns.

  Examples:
    >>> _friendly_words(["frame", "usable"])
    'DataFrame usable'
  """
  out: list[str] = []
  for p in parts:
    low = p.lower()
    if low in ("df", "frame"):
      out.append("DataFrame")
    elif low == "qs":
      out.append("queryset")
    elif low == "bw":
      out.append("bandwidth")
    elif low == "fn":
      out.append("function")
    elif low == "cfg":
      out.append("config")
    elif low == "jid":
      out.append("job id")
    elif low == "acct":
      out.append("accounting")
    else:
      out.append(p)
  return " ".join(out)


def _default_behavior_summary(name: str, *, trivial: bool) -> str:
  """Build a human one-line summary from an identifier.

  Args:
    name (str): Function or class identifier.
    trivial (bool): True when ``name`` is a trivial dunder.

  Returns:
    str: Summary without trailing period.

  Examples:
    >>> _default_behavior_summary("_frame_usable", trivial=False)
    'Internal helper to check if the DataFrame is usable'
    >>> _default_behavior_summary("build_qs", trivial=False)
    'Build the queryset'
  """
  from python_def_inventory import summary_echoes_name

  short_methods = {
    "start": "Start background work for this object",
    "stop": "Stop background work for this object",
    "shutdown": "Shut down this object and release resources",
    "stats": "Return statistics for this object",
    "phase": "Return the current phase for this object",
    "add": "Add an entry to this collection",
    "delete": "Delete a key from this store",
    "submit": "Submit work to this executor",
    "publish": "Publish state for downstream consumers",
    "complete": "Mark this unit of work complete",
    "finish": "Finish processing and finalize state",
    "take": "Take the next item from this partition",
    "json": "Return the JSON-serializable payload",
    "render": "Render the response body",
    "generic": "Build a generic metadata entry",
    "close": "Close this object and release resources",
    "open": "Open this object for use",
    "run": "Run this object's main workflow",
    "update": "Update this object's state",
    "clear": "Clear this object's stored state",
    "reset": "Reset this object to its initial state",
    "flush": "Flush buffered state for this object",
    "join": "Wait for this object's workers to finish",
  }

  if trivial:
    result = TRIVIAL_DUNDER_SUMMARIES.get(
      name,
      f"Implement the ``{name}`` protocol",
    )
    return result
  if name in short_methods:
    return short_methods[name]
  if name == "handle":
    return "Run the command or handler body"
  if name == "Meta":
    return "Django model metadata for the enclosing model"
  if name == "main":
    return "Run this module's command-line entrypoint"

  private = name.startswith("_")
  parts = [p for p in name.strip("_").split("_") if p]
  if not parts:
    return "Perform the operation for this symbol"

  if name[:1].isupper() and not name.startswith("_"):
    label = _friendly_words(parts)
    return f"Hold {label} state and behavior"

  if parts[0] in ("is", "has", "can", "should"):
    rest = _friendly_words(parts[1:]) or "condition"
    if parts[0] == "is":
      core = f"check if {rest}"
    elif parts[0] == "has":
      core = f"check whether {rest} is present"
    else:
      core = f"check whether we {parts[0]} {rest}"
    result = f"Internal helper to {core}" if private else f"Return True if {rest}"
  elif parts[-1] in ("usable", "valid", "ready", "present", "empty", "ok"):
    subject = _friendly_words(parts[:-1]) or "value"
    core = f"check if the {subject} is {parts[-1]}"
    result = (
      f"Internal helper to {core}"
      if private
      else f"Return True if the {subject} is {parts[-1]}"
    )
  elif parts[0] == "visit":
    target = parts[-1] if len(parts) > 1 else "node"
    result = f"Visit a ``{target}`` node while walking the AST"
  elif parts[0] in _VERB_LEADERS:
    verb = _VERB_LEADERS[parts[0]]
    obj = _friendly_words(parts[1:])
    if obj:
      if verb == "return":
        phrase = f"return the {obj}"
      else:
        phrase = f"{verb} the {obj}"
    else:
      phrase = verb
    if private:
      result = f"Internal helper to {phrase}"
    else:
      result = phrase[0].upper() + phrase[1:]
  else:
    phrase = _friendly_words(parts)
    if private:
      result = f"Internal helper to handle {phrase}"
    else:
      result = phrase[0].upper() + phrase[1:] if phrase else name

  if summary_echoes_name(result + ".", name):
    return short_methods.get(name, f"{result} for this object")
  return result

def _summary_from_existing(doc: str | None, name: str, trivial: bool) -> str:
  """
  Choose a one-line summary from an existing docstring or a default.
  
  Args:
    doc (str | None): One of ``str``, ``None``.
    name (str): String for name.
    trivial (bool): Boolean flag for trivial.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _summary_from_existing(None, "x", True)  # doctest: +SKIP
  """
  from python_def_inventory import summary_echoes_name, summary_is_ai_slop

  if doc and doc.strip():
    first = doc.strip().splitlines()[0].strip()
    if first.endswith(":") and first[:-1] in (
      "Args",
      "Returns",
      "Yields",
      "Arguments",
      "Parameters",
    ):
      first = ""
    if first:
      summary = first.rstrip(".")
      kind = (
        "class"
        if name[:1].isupper() and not name.startswith("_")
        else "function"
      )
      if (
        not summary_echoes_name(summary + ".", name)
        and not summary_is_ai_slop(summary, kind=kind)
        and "internal helper for " not in summary.lower()
        and "compute or apply " not in summary.lower()
      ):
        return summary
  return _default_behavior_summary(name, trivial=trivial)


def _example_literal_for_ann(ann: str, bare: str) -> str:
  """Pick a small literal for Examples based on an annotation string.

  Args:
    ann (str): Annotation source text.
    bare (str): Parameter name without stars.

  Returns:
    str: Python literal source fragment.

  Examples:
    >>> _example_literal_for_ann("int", "n")
    '0'
  """
  flat = re.sub(r"\s+", "", ann)
  low = bare.lower()
  if flat.startswith("Optional[") or flat.endswith("|None"):
    return "None"
  if "bool" in flat.lower() or low.startswith(("is_", "has_", "use_", "enable_")):
    return "True"
  if flat in ("int", "float") or flat.startswith("int") or "int|" in flat:
    return "0"
  if flat in ("str",) or flat.startswith("str") or "Path" in flat:
    return '"x"'
  if flat.startswith(("list", "List", "tuple", "Tuple", "Sequence", "set", "Set")):
    return "[]"
  if flat.startswith(("dict", "Dict", "Mapping")):
    return "{}"
  if flat in ("None",):
    return "None"
  return "None"


def _build_example_call(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  *,
  source: str,
  class_name: str | None = None,
) -> str:
  """Build a concrete ``>>>`` example call line (no indent).

  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): Function AST node.
    source (str): Module source for annotation slices.
    class_name (str | None): Enclosing class name for methods.

  Returns:
    str: Example line starting with ``>>>``.

  Examples:
    >>> _build_example_call(
    ...     __import__("ast").parse("def f(x: int) -> int:\\n  return x\\n").body[0],
    ...     source="def f(x: int) -> int:\\n  return x\\n",
    ... )
    '>>> f(0)  # doctest: +SKIP'
  """
  anns = _param_annotations(fn, source)
  args: list[str] = []
  for name in documentable_params(fn):
    if name.startswith("**"):
      if name.lstrip("*") == "options":
        args.append("verbosity=1")
      continue
    if name.startswith("*"):
      continue
    args.append(_example_literal_for_ann(anns.get(name, "Any"), name.lstrip("*")))
  joined = ", ".join(args)
  if class_name and fn.name == "__init__":
    call = f"{class_name}({joined})"
  elif class_name and not fn.name.startswith("__"):
    call = f"{class_name}().{fn.name}({joined})"
  else:
    call = f"{fn.name}({joined})"
  line = f">>> {call}  # doctest: +SKIP"
  if len(line) > 76:
    line = f">>> {call}"
    if len(line) > 76:
      line = f">>> {fn.name}(0)  # doctest: +SKIP"
  return line


def build_docstring_lines(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  *,
  source: str,
  class_name: str | None = None,
  base_names: Sequence[str] | None = None,
) -> list[str]:
  """Build Google-style docstring content lines (no quotes/indent).

  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): Function AST node.
    source (str): Module source for annotation slices.
    class_name (str | None): Enclosing class name for methods.
    base_names (Sequence[str] | None): Enclosing class base names.

  Returns:
    list[str]: Docstring content lines without quotes or indent.

  Examples:
    >>> build_docstring_lines(
    ...     __import__("ast").parse("def f(x: int) -> int:\\n  return x\\n").body[0],
    ...     source="def f(x: int) -> int:\\n  return x\\n",
    ... )[0]
    'F.'
  """
  existing = ast.get_docstring(fn)
  trivial = fn.name in TRIVIAL_DUNDERS
  summary = _summary_from_existing(existing, fn.name, trivial)
  anns = _param_annotations(fn, source)
  params = documentable_params(fn)
  ret = (_expr_source(source, fn.returns) or infer_return_type(fn)).strip()
  ret = re.sub(r"\s+", " ", ret)

  lines = [summary + "."]
  bases = list(base_names or [])
  if fn.name == "handle" and any("BaseCommand" in b for b in bases):
    lines.append("")
    lines.append(
      "Override of Django ``BaseCommand.handle``, the hook called after "
      "option parsing; subclasses implement command work here."
    )
  if existing:
    ext: list[str] = []
    for raw in existing.strip().splitlines()[1:]:
      if re.match(
        r"^(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|"
        r"Examples|Example|Attributes):",
        raw.strip(),
      ):
        break
      bad_tokens = (
        "Polymorphic/legacy",
        "polymorphic when",
        "consult call sites",
        "see return sites",
        "see callers",
        "call site",
        "call-site",
        "Open polymorphism for",
        "Open return polymorphism",
        "Keyword-arg polymorphism",
        "Variadic positional polymorphism",
        "Value polymorphism",
        "Internal helper for ",
        "Compute or apply ",
        "Result of ``",
      )
      if any(t in raw for t in bad_tokens):
        continue
      ext.append(raw.rstrip())
    while ext and not ext[0].strip():
      ext.pop(0)
    while ext and not ext[-1].strip():
      ext.pop()
    if ext:
      lines.append("")
      lines.extend(ext)

  if params:
    lines.append("")
    lines.append("Args:")
    for name in params:
      ann = anns.get(name, "Any")
      ann_flat = re.sub(r"\s+", " ", ann)
      lines.append(f"  {name} ({ann_flat}): {_describe_param(name, ann_flat)}")

  lines.append("")
  if _is_generator(fn):
    lines.append("Yields:")
    lines.append(f"  {ret}: {_describe_return(fn.name, ret)}")
  else:
    lines.append("Returns:")
    if ret == "None":
      lines.append("  None")
    else:
      lines.append(f"  {ret}: {_describe_return(fn.name, ret)}")

  raised = collect_raised_exception_names(fn)
  if raised:
    lines.append("")
    lines.append("Raises:")
    for exc in raised:
      lines.append(
        f"  {exc}: Raised when ``{fn.name}`` hits a ``{exc}`` failure path."
      )

  lines.append("")
  lines.append("Examples:")
  lines.append(
    "  "
    + _build_example_call(fn, source=source, class_name=class_name)
  )
  return lines


def build_module_docstring_lines(
  tree: ast.Module,
  *,
  module_name: str,
) -> list[str]:
  """
  Build a module-level Google docstring with Attributes when needed.
  
  Preserves multi-paragraph prose from an existing module docstring.
  
  Args:
    tree (ast.Module): Tree.
    module_name (str): String for module name.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> build_module_docstring_lines(None, "x")  # doctest: +SKIP
  """
  existing = ast.get_docstring(tree)
  summary = _summary_from_existing(existing, module_name, False)
  lines = [summary + "."]
  if existing:
    ext: list[str] = []
    for raw in existing.strip().splitlines()[1:]:
      if re.match(
        r"^(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|"
        r"Examples|Example|Attributes):",
        raw.strip(),
      ):
        break
      ext.append(raw.rstrip())
    while ext and not ext[0].strip():
      ext.pop(0)
    while ext and not ext[-1].strip():
      ext.pop()
    if ext:
      lines.append("")
      lines.extend(ext)
  attrs = collect_module_level_attrs(tree)
  if attrs:
    lines.append("")
    lines.append("Attributes:")
    for name in attrs:
      lines.append(f"  {name}: ``{name}``.")
  return lines


def _base_name(node: ast.expr) -> str:
  """Return a dotted-ish base name for a class base expression.

  Args:
    node (ast.expr): Base expression AST node.

  Returns:
    str: Simple or attribute name, else empty.

  Examples:
    >>> _base_name(__import__("ast").parse("class C(Base):\\n  pass\\n").body[0].bases[0])
    'Base'
  """
  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    return node.attr
  return ""


def _class_help_text(cls: ast.ClassDef) -> str | None:
  """Return the string value of a Django-style ``help = "..."`` class attr.

  Args:
    cls (ast.ClassDef): Class AST node.

  Returns:
    str | None: Help text when present.

  Examples:
    >>> _class_help_text(__import__("ast").parse("class C:\\n  pass\\n").body[0])
  """
  for stmt in cls.body:
    if isinstance(stmt, ast.Assign):
      for t in stmt.targets:
        if isinstance(t, ast.Name) and t.id == "help":
          if isinstance(stmt.value, ast.Constant) and isinstance(
            stmt.value.value, str
          ):
            return stmt.value.value
  return None


def build_class_docstring_lines(cls: ast.ClassDef) -> list[str]:
  """Build a class Google docstring with Attributes for instance fields.

  Preserves multi-paragraph prose from an existing class docstring.

  Args:
    cls (ast.ClassDef): Class node.

  Returns:
    list[str]: Docstring content lines without quotes or indent.

  Examples:
    >>> build_class_docstring_lines(
    ...     __import__("ast").parse("class Box:\\n  pass\\n").body[0]
    ... )[0]
    'Hold Box state and behavior.'
  """
  existing = ast.get_docstring(cls)
  help_text = _class_help_text(cls)
  if help_text and (
    not existing
    or summary_echoes_name_safe(existing, cls.name)
  ):
    summary = help_text.rstrip(".")
  else:
    summary = _summary_from_existing(existing, cls.name, False)
  lines = [summary + "."]
  bases = [_base_name(b) for b in cls.bases if _base_name(b)]
  if bases:
    base = bases[0]
    if base == "BaseCommand":
      lines.append("")
      lines.append(
        "Subclasses Django ``BaseCommand``, the framework entry for "
        "``manage.py`` commands (argument parsing, ``handle`` dispatch, "
        "stdout/stderr)."
      )
    elif base not in ("object", "Exception", "BaseException"):
      lines.append("")
      lines.append(
        f"Subclasses ``{base}``, extending that type with this class's "
        "fields and behavior."
      )
  if existing:
    ext: list[str] = []
    for raw in existing.strip().splitlines()[1:]:
      if re.match(
        r"^(Args|Arguments|Parameters|Returns|Return|Yields|Yield|Raises|"
        r"Examples|Example|Attributes|Note|Notes):",
        raw.strip(),
      ):
        break
      bad_tokens = (
        "call site",
        "call-site",
        "see callers",
        "see return sites",
      )
      if any(t in raw for t in bad_tokens):
        continue
      ext.append(raw.rstrip())
    while ext and not ext[0].strip():
      ext.pop(0)
    while ext and not ext[-1].strip():
      ext.pop()
    if ext:
      lines.append("")
      lines.extend(ext)
  attrs = collect_class_instance_attrs(cls)
  if attrs:
    lines.append("")
    lines.append("Attributes:")
    for name in attrs:
      lines.append(f"  {name}: ``{name}``.")
  return lines


def summary_echoes_name_safe(doc: str, name: str) -> bool:
  """Return True when the docstring summary echoes ``name``.

  Args:
    doc (str): Full docstring text.
    name (str): Class or function name.

  Returns:
    bool: True when the summary is a bare name echo.

  Examples:
    >>> summary_echoes_name_safe("Command.", "Command")
    True
  """
  from python_def_inventory import docstring_summary_line, summary_echoes_name

  return summary_echoes_name(docstring_summary_line(doc), name)


def format_docstring_block(
  content_lines: list[str],
  indent: str,
  *,
  line_length: int = 80,
) -> str:
  """
  Format docstring content as an indented triple-quoted block.
  
  Args:
    content_lines (list[str]): Sequence for content lines.
    indent (str): String for indent.
    line_length (int): Integer value for line length.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> format_docstring_block([], "x", 0)  # doctest: +SKIP
  """
  wrapped = _wrap_doc_content_lines(
    content_lines, indent=indent, line_length=line_length
  )
  out = [f'{indent}"""\n']
  for line in wrapped:
    out.append(f"{indent}{line}\n" if line else f"{indent}\n")
  out.append(f'{indent}"""\n')
  return "".join(out)


def _format_param(name: str, ann: str | None, default_src: str | None) -> str:
  """
  Format one parameter for a reconstructed signature.
  
  Args:
    name (str): String for name.
    ann (str | None): One of ``str``, ``None``.
    default_src (str | None): One of ``str``, ``None``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _format_param("x", None, None)  # doctest: +SKIP
  """
  piece = name
  if ann:
    piece += f": {ann}"
  if default_src is not None:
    piece += f" = {default_src}"
  return piece


def _collect_signature_parts(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  param_anns: dict[str, str],
  source: str,
) -> list[str]:
  """
  Collect comma-separated signature parameter fragments.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
    param_anns (dict[str, str]): Mapping for param anns.
    source (str): String for source.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _collect_signature_parts(None, {}, "x")  # doctest: +SKIP
  """
  args = fn.args
  parts: list[str] = []
  all_pos = list(args.posonlyargs) + list(args.args)
  defaults = list(args.defaults)
  pad = len(all_pos) - len(defaults)
  skip_first = bool(all_pos and all_pos[0].arg in ("self", "cls"))

  def default_for_index(i: int) -> str | None:
    """
    Default for index.
    
    Args:
      i (int): Integer value for i.
    
    Returns:
      str | None: One of ``str``, ``None`` depending on inputs/branch.
    
    Examples:
      >>> default_for_index(0)  # doctest: +SKIP
    """
    if i < pad:
      return None
    return _expr_source(source, defaults[i - pad])

  for i, arg in enumerate(args.posonlyargs):
    if skip_first and i == 0:
      existing = _expr_source(source, arg.annotation)
      ann = existing.strip() if existing else None
    else:
      ann = param_anns.get(arg.arg)
    parts.append(_format_param(arg.arg, ann, default_for_index(i)))
  if args.posonlyargs:
    parts.append("/")

  for i, arg in enumerate(args.args):
    idx = len(args.posonlyargs) + i
    if skip_first and idx == 0:
      existing = _expr_source(source, arg.annotation)
      ann = existing.strip() if existing else None
      parts.append(_format_param(arg.arg, ann, default_for_index(idx)))
      continue
    parts.append(
      _format_param(arg.arg, param_anns.get(arg.arg), default_for_index(idx))
    )

  if args.vararg is not None:
    key = f"*{args.vararg.arg}"
    parts.append(_format_param(f"*{args.vararg.arg}", param_anns.get(key), None))
  elif args.kwonlyargs:
    parts.append("*")

  for arg, default in zip(args.kwonlyargs, args.kw_defaults):
    dsrc = _expr_source(source, default) if default is not None else None
    parts.append(_format_param(arg.arg, param_anns.get(arg.arg), dsrc))

  if args.kwarg is not None:
    key = f"**{args.kwarg.arg}"
    parts.append(_format_param(f"**{args.kwarg.arg}", param_anns.get(key), None))
  return parts


def _emit_signature_param_lines(
  part: str,
  *,
  cont: str,
  line_length: int,
) -> list[str]:
  """
  Emit one signature parameter, wrapping long annotations if needed.
  
  Args:
    part (str): String for part.
    cont (str): String for cont.
    line_length (int): Integer value for line length.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _emit_signature_param_lines("x", "x", 0)  # doctest: +SKIP
  """
  suffix = ","
  full = f"{cont}{part}{suffix}"
  if part in ("/", "*") or len(full) <= line_length:
    return [full]

  m = re.match(r"^(\*{0,2}\w+)\s*:\s*(.*)$", part)
  if not m:
    return [full]

  name, rest = m.group(1), m.group(2)
  default: str | None = None
  if " = " in rest:
    ann, default = rest.rsplit(" = ", 1)
  else:
    ann = rest

  unit = "\t" if "\t" in cont else "  "
  inner = cont + unit
  out = [f"{cont}{name}: ("]
  width = max(20, line_length - len(inner))
  wrapped = textwrap.wrap(
    ann,
    width=width,
    break_long_words=True,
    break_on_hyphens=False,
  ) or [ann]
  for chunk in wrapped:
    out.append(f"{inner}{chunk}")
  if default is not None:
    closer = f"{cont}) = {default}{suffix}"
    if len(closer) <= line_length:
      out.append(closer)
    else:
      out.append(f"{cont}) =")
      out.append(f"{inner}{default}{suffix}")
  else:
    out.append(f"{cont}){suffix}")
  return out


def _signature_block(
  fn: ast.FunctionDef | ast.AsyncFunctionDef,
  param_anns: dict[str, str],
  return_ann: str,
  source: str,
  *,
  base_indent: str,
  line_length: int,
) -> str:
  """
  Build a ``def``/``async def`` header, wrapping past ``line_length``.
  
  Args:
    fn (ast.FunctionDef | ast.AsyncFunctionDef): One of ``ast.FunctionDef``,
    ``ast.AsyncFunctionDef``.
    param_anns (dict[str, str]): Mapping for param anns.
    return_ann (str): String for return ann.
    source (str): String for source.
    base_indent (str): String for base indent.
    line_length (int): Integer value for line length.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _signature_block(None, {}, "x", "x", "x", 0)  # doctest: +SKIP
  """
  parts = _collect_signature_parts(fn, param_anns, source)
  return_ann = re.sub(r"\s+", " ", return_ann)
  kw = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
  unit = "\t" if "\t" in base_indent else "  "
  # Wrapped params use one indent unit past the def line.
  cont = base_indent + unit
  one = f"{kw} {fn.name}({', '.join(parts)}) -> {return_ann}:"
  if len(base_indent + one) <= line_length:
    return base_indent + one + "\n"

  lines = [f"{base_indent}{kw} {fn.name}("]
  for part in parts:
    lines.extend(
      _emit_signature_param_lines(
        part,
        cont=cont,
        line_length=line_length,
      )
    )
  close = f"{base_indent}) -> {return_ann}:"
  if len(close) <= line_length:
    lines.append(close)
  else:
    # Keep syntax valid: parenthesize a long return annotation.
    lines.append(f"{base_indent}) -> (")
    width = max(20, line_length - len(cont))
    for chunk in textwrap.wrap(
      return_ann,
      width=width,
      break_long_words=True,
      break_on_hyphens=False,
    ):
      lines.append(f"{cont}{chunk}")
    lines.append(f"{base_indent}):")
  return "\n".join(lines) + "\n"


def _ensure_future_annotations(source: str) -> str:
  """
  Insert ``from __future__ import annotations`` when missing.
  
  Args:
    source (str): String for source.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _ensure_future_annotations("x")  # doctest: +SKIP
  """
  if re.search(r"from __future__ import annotations", source):
    return source
  lines = source.splitlines(keepends=True)
  idx = 0
  if lines and lines[0].startswith("#!"):
    idx = 1
  if idx < len(lines) and re.match(r"^#.*coding[:=]", lines[idx]):
    idx += 1
  # After module docstring if present.
  try:
    tree = ast.parse(source)
    if (
      tree.body
      and isinstance(tree.body[0], ast.Expr)
      and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
      and isinstance(tree.body[0].value.value, str)
    ):
      idx = tree.body[0].end_lineno or idx
  except SyntaxError:
    pass
  lines.insert(idx, "from __future__ import annotations\n")
  if idx + 1 < len(lines) and lines[idx + 1].strip():
    lines.insert(idx + 1, "\n")
  return "".join(lines)


def _ensure_typing_imports(source: str, needed: set[str]) -> str:
  """
  Ensure ``typing`` names used in annotations are imported.
  
  Args:
    source (str): String for source.
    needed (set[str]): Sequence for needed.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _ensure_typing_imports("x", [])  # doctest: +SKIP
  """
  missing = {
    name
    for name in needed
    if not re.search(rf"\bfrom typing import[^\n]*\b{name}\b", source)
  }
  if not missing:
    return source
  m = re.search(r"^from typing import (.+)$", source, re.MULTILINE)
  if m:
    existing = {p.strip() for p in m.group(1).split(",")}
    existing |= missing
    new_line = "from typing import " + ", ".join(sorted(existing))
    return source[: m.start()] + new_line + source[m.end() :]
  line = "from typing import " + ", ".join(sorted(missing)) + "\n"
  if "from __future__ import annotations\n" in source:
    return source.replace(
      "from __future__ import annotations\n",
      "from __future__ import annotations\n\n" + line,
      1,
    )
  return line + "\n" + source


def _replace_leading_docstring(
  source: str,
  node: ast.AST,
  new_doc_block: str,
  *,
  base_indent: str,
) -> str | None:
  """
  Replace or insert a docstring immediately under ``node``.
  
  Args:
    source (str): String for source.
    node (ast.AST): Node.
    new_doc_block (str): String for new doc block.
    base_indent (str): String for base indent.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> _replace_leading_docstring("x", None, "x", "x")  # doctest: +SKIP
  """
  lines = source.splitlines(keepends=True)
  body = getattr(node, "body", None)
  if not body:
    return None
  first = body[0]
  has_doc = (
    isinstance(first, ast.Expr)
    and isinstance(getattr(first, "value", None), ast.Constant)
    and isinstance(first.value.value, str)
  )
  if isinstance(node, ast.Module):
    insert_at = 0
    # Keep shebang / encoding cookie before module docstring.
    if lines and lines[0].startswith("#!"):
      insert_at = 1
    if insert_at < len(lines) and re.match(r"^#.*coding[:=]", lines[insert_at]):
      insert_at += 1
    if has_doc:
      end = first.end_lineno or first.lineno
      new_lines = new_doc_block.splitlines(keepends=True)
      if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
      # Module docstring occupies lines first.lineno..end (1-based).
      lines[first.lineno - 1 : end] = new_lines
      # Ensure blank line after docstring when next stmt follows tightly.
      return "".join(lines)
    new_lines = new_doc_block.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
      new_lines[-1] += "\n"
    if insert_at < len(lines) and lines[insert_at].strip():
      new_lines = new_lines + ["\n"]
    lines[insert_at:insert_at] = new_lines
    return "".join(lines)

  # Class body docstring.
  if has_doc:
    end = first.end_lineno or first.lineno
    new_lines = new_doc_block.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
      new_lines[-1] += "\n"
    lines[first.lineno - 1 : end] = new_lines
    return "".join(lines)
  # Insert after class header; prefer first body lineno as insertion point.
  insert_idx = first.lineno - 1
  new_lines = new_doc_block.splitlines(keepends=True)
  if new_lines and not new_lines[-1].endswith("\n"):
    new_lines[-1] += "\n"
  lines[insert_idx:insert_idx] = new_lines
  return "".join(lines)


def upgrade_source(
  source: str,
  *,
  rel_path: str,
  force_docs: bool = False,
  line_length: int = 80,
) -> tuple[str, int]:
  """
  Upgrade all in-scope functions, classes, and the module docstring.
  
  Applies edits from the bottom of the file upward so line numbers remain
  valid for earlier functions.
  
  Args:
    source (str): String for source.
    rel_path (str): String for rel path.
    force_docs (bool): Whether to enable force docs.
    line_length (int): Integer value for line length.
  
  Returns:
    tuple[str, int]: tuple[str, int] produced by this call.
  
  Examples:
    >>> upgrade_source("x", "x", True, 0)  # doctest: +SKIP
  """
  if exclusion_reason_for_path(Path(rel_path)) is not None:
    return source, 0

  source = _ensure_future_annotations(source)
  try:
    tree = ast.parse(source)
  except SyntaxError:
    return source, 0

  targets: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

  class Collector(ast.NodeVisitor):
    """
    Hold Collector state and behavior.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    """
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      """
      Visit a ``FunctionDef`` node while walking the AST.
      
      Args:
        node (ast.FunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> Collector().visit_FunctionDef(None)  # doctest: +SKIP
      """
      targets.append(node)
      self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      """
      Visit a ``AsyncFunctionDef`` node while walking the AST.
      
      Args:
        node (ast.AsyncFunctionDef): Node.
      
      Returns:
        None
      
      Examples:
        >>> Collector().visit_AsyncFunctionDef(None)  # doctest: +SKIP
      """
      targets.append(node)
      self.generic_visit(node)

  Collector().visit(tree)

  # Map each function node id -> enclosing ClassDef (if any).
  fn_to_class: dict[int, ast.ClassDef] = {}

  class ClassMap(ast.NodeVisitor):
    """
    Walk classes to record enclosing context for methods.
    
    Subclasses ``NodeVisitor``, extending that type with this class's fields and
    behavior.
    
    Attributes:
      _stack: ``_stack``.
    """

    def __init__(self) -> None:
      """
      Initialize a new instance.
      
      Returns:
        None
      
      Examples:
        >>> ClassMap()  # doctest: +SKIP
      """
      self._stack: list[ast.ClassDef] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
      """Visit a class and push it for nested methods.

      Args:
        node (ast.ClassDef): Class AST node.

      Returns:
        None

      Examples:
        >>> ClassMap().visit(__import__("ast").parse("class C:\\n  pass\\n"))
      """
      self._stack.append(node)
      self.generic_visit(node)
      self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      """Record enclosing class for a method.

      Args:
        node (ast.FunctionDef): Function AST node.

      Returns:
        None

      Examples:
        >>> ClassMap().visit_FunctionDef(
        ...     __import__("ast").parse("def f():\\n  pass\\n").body[0]
        ... )
      """
      if self._stack:
        fn_to_class[id(node)] = self._stack[-1]
      self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      """Record enclosing class for an async method.

      Args:
        node (ast.AsyncFunctionDef): Async function AST node.

      Returns:
        None

      Examples:
        >>> ClassMap().visit_AsyncFunctionDef(
        ...     __import__("ast").parse("async def f():\\n  pass\\n").body[0]
        ... )
      """
      if self._stack:
        fn_to_class[id(node)] = self._stack[-1]
      self.generic_visit(node)

  ClassMap().visit(tree)

  needed_typing: set[str] = set()
  edits: list[tuple[int, int, str]] = []
  lines = source.splitlines(keepends=True)
  touched = 0

  for fn in targets:
    trivial = fn.name in TRIVIAL_DUNDERS
    sig_i = signature_annotation_issues(fn)
    doc_i = docstring_issues(fn, trivial_dunder=trivial)
    first_body = fn.body[0] if fn.body else None
    if first_body is None:
      continue
    base_indent = re.match(r"^[ \t]*", lines[fn.lineno - 1]).group(0)
    sig_line_end = first_body.lineno - 1  # exclusive index into lines
    needs_sig_wrap = any(
      len(lines[i]) > line_length for i in range(fn.lineno - 1, max(fn.lineno, sig_line_end))
    )
    existing_doc = ast.get_docstring(fn) or ""
    stale_poly_docs = any(
      t in existing_doc
      for t in (
        "see callers",
        "see return sites",
        "consult call sites",
        "Polymorphic/legacy",
        "call site",
        "call-site",
        "Keyword-arg polymorphism",
        "Variadic positional polymorphism",
        "(...)  # doctest: +SKIP",
        ">>> ...  # doctest: +SKIP",
        "Internal helper for ",
        "Compute or apply ",
        "Value polymorphism",
        "Open polymorphism",
        "Result of ``",
        " as ``",
      )
    )
    if (
      not force_docs
      and not sig_i
      and not doc_i
      and not needs_sig_wrap
      and not stale_poly_docs
    ):
      continue

    param_anns = _param_annotations(fn, source)
    for ann in param_anns.values():
      if "Any" in ann:
        needed_typing.add("Any")
      if "Iterator" in ann:
        needed_typing.add("Iterator")
    ret = (_expr_source(source, fn.returns) or infer_return_type(fn)).strip()
    ret = re.sub(r"\s+", " ", ret)
    if "Any" in ret:
      needed_typing.add("Any")
    if "Iterator" in ret:
      needed_typing.add("Iterator")

    sig_text = _signature_block(
      fn,
      param_anns,
      ret,
      source,
      base_indent=base_indent,
      line_length=line_length,
    )
    body_line = lines[first_body.lineno - 1]
    body_indent = re.match(r"^[ \t]*", body_line).group(0)
    if len(body_indent) <= len(base_indent):
      unit = "\t" if "\t" in base_indent else "  "
      body_indent = base_indent + unit
    enclosing = fn_to_class.get(id(fn))
    class_name = enclosing.name if enclosing else None
    base_names = (
      [_base_name(b) for b in enclosing.bases if _base_name(b)]
      if enclosing
      else []
    )
    doc_lines = build_docstring_lines(
      fn,
      source=source,
      class_name=class_name,
      base_names=base_names,
    )
    doc_block = format_docstring_block(
      doc_lines, body_indent, line_length=line_length
    )

    sig_start = fn.lineno - 1
    body_start = first_body.lineno - 1

    has_doc = (
      isinstance(first_body, ast.Expr)
      and isinstance(getattr(first_body, "value", None), ast.Constant)
      and isinstance(first_body.value.value, str)
    )
    if has_doc:
      doc_end = first_body.end_lineno or first_body.lineno
      replace_end = doc_end
      new_seg = sig_text + doc_block
      edits.append((sig_start, replace_end, new_seg))
    else:
      new_seg = sig_text + doc_block
      edits.append((sig_start, body_start, new_seg))
  edits.sort(key=lambda e: e[0], reverse=True)
  for start, end, seg in edits:
    new_lines = seg.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
      new_lines[-1] = new_lines[-1] + "\n"
    lines[start:end] = new_lines
  updated = "".join(lines)
  updated = _ensure_typing_imports(updated, needed_typing)
  touched += len(edits)

  # Class + module surface (re-parse after function edits).
  try:
    tree2 = ast.parse(updated)
  except SyntaxError:
    return updated, touched

  class_nodes = sorted(
    (n for n in ast.walk(tree2) if isinstance(n, ast.ClassDef)),
    key=lambda n: n.lineno,
    reverse=True,
  )
  for cls in class_nodes:
    issues = class_surface_issues(cls)
    if not force_docs and not issues:
      continue
    split_lines = updated.splitlines(keepends=True)
    cls_line = split_lines[cls.lineno - 1]
    base_indent = re.match(r"^[ \t]*", cls_line).group(0)
    body_indent = base_indent + ("\t" if "\t" in base_indent else "  ")
    if cls.body:
      first_stmt_line = split_lines[cls.body[0].lineno - 1]
      m_body = re.match(r"^[ \t]*", first_stmt_line)
      if m_body and len(m_body.group(0)) > len(base_indent):
        body_indent = m_body.group(0)
    doc_block = format_docstring_block(
      build_class_docstring_lines(cls),
      body_indent,
      line_length=line_length,
    )
    replaced = _replace_leading_docstring(
      updated, cls, doc_block, base_indent=base_indent
    )
    if replaced and replaced != updated:
      updated = replaced
      touched += 1

  try:
    tree3 = ast.parse(updated)
  except SyntaxError:
    return updated, touched
  mod_issues = module_surface_issues(tree3)
  if force_docs or mod_issues:
    mod_name = Path(rel_path).stem
    doc_block = format_docstring_block(
      build_module_docstring_lines(tree3, module_name=mod_name),
      "",
      line_length=line_length,
    )
    replaced = _replace_leading_docstring(
      updated, tree3, doc_block, base_indent=""
    )
    if replaced and replaced != updated:
      updated = replaced
      touched += 1

  return updated, touched


def upgrade_path(
  path: Path,
  *,
  root: Path,
  apply: bool,
  force_docs: bool = False,
  line_length: int = 80,
) -> int:
  """
  Upgrade one file on disk.
  
  Args:
    path (Path): String for path.
    root (Path): String for root.
    apply (bool): Boolean flag for apply.
    force_docs (bool): Whether to enable force docs.
    line_length (int): Integer value for line length.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> upgrade_path("x", "x", True, True, 0)  # doctest: +SKIP
  """
  rel = str(path.relative_to(root)).replace("\\", "/")
  if exclusion_reason_for_path(Path(rel)) is not None:
    return 0
  original = path.read_text(encoding="utf-8")
  updated, touched = upgrade_source(
    original,
    rel_path=rel,
    force_docs=force_docs,
    line_length=line_length,
  )
  if touched and apply and updated != original:
    path.write_text(updated, encoding="utf-8")
  return touched


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry for docstring/annotation upgrades.
  
  Args:
    argv (Sequence[str] | None): One of ``Sequence[str]``, ``None``.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> main(None)  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--root", action="append", type=Path, default=None)
  parser.add_argument("--workspace-root", type=Path, default=None)
  parser.add_argument("--path-filter", default=None)
  parser.add_argument(
    "--apply",
    action="store_true",
    help="Write changes (default is dry-run count only).",
  )
  parser.add_argument(
    "--force-docs",
    action="store_true",
    help="Rewrite docstrings even when already compliant (rewrap).",
  )
  parser.add_argument(
    "--line-length",
    type=int,
    default=None,
    help="Docstring wrap width (default: 80 for HPCPerfStats, 88 for tools).",
  )
  args = parser.parse_args(list(argv) if argv is not None else None)
  roots = list(args.root) if args.root else default_roots(args.workspace_root)
  total = 0
  files = 0
  for root in roots:
    root = root.resolve()
    line_length = args.line_length
    if line_length is None:
      line_length = 88 if root.name == "hpcperfstats-tools" else 80
    for path in sorted(root.rglob("*.py")):
      if any(
        p in {".git", ".venv", "__pycache__", "node_modules", "test_runs"}
        or p.endswith(".egg-info")
        for p in path.parts
      ):
        continue
      rel = str(path.relative_to(root)).replace("\\", "/")
      if args.path_filter and args.path_filter not in rel:
        continue
      if exclusion_reason_for_path(Path(rel)):
        continue
      n = upgrade_path(
        path,
        root=root,
        apply=args.apply,
        force_docs=args.force_docs,
        line_length=line_length,
      )
      if n:
        files += 1
        total += n
        print(f"{rel}: {n} def(s)")
  mode = "applied" if args.apply else "dry-run"
  print(f"{mode}: {total} defs across {files} files")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
