#!/usr/bin/env python3
"""
AST inventory of sync_timedb negative states and gate sites (Phase 1b).

Walks ``hpcperfstats/dbload/**/*.py`` (Codon compile units excluded) and
emits, as JSON:

* ``values`` - every string assigned or passed as ``status`` / ``outcome`` /
  ``reason`` / ``kind`` / ``phase`` / ``verify_stage`` (keyword argument,
  dict literal key, or ``x["status"] = ...`` subscript). Non-constant values
  (f-strings, names, calls) are recorded under ``dynamic`` with their
  location so each one can be resolved by hand.
* ``manifest_keys`` - string keys written on ``_manifest[...]``.
* ``shard_fields`` - attributes assigned on ``_DayShard`` instances.
* ``call_sites`` - every call to a requeue / ACK / dead-letter / sticky-flag
  setter / quarantine function, keyed by ``file:function:callee``.

The drift guard (``test_sync_timedb_state_doc_registry.py``) runs
``build_inventory`` and fails when ``docs/SYNC_TIMEDB_STATE_MACHINE.md`` does
not mention an inventoried value or lacks a gate-closure row for a call site,
so exhaustiveness holds for future code, not only today's.

Attributes:
  DYNAMIC_BEGIN: Start marker of the generated doc block.
  DYNAMIC_END: End marker of the generated doc block.
  GATE_CALLEES: Callee names whose call sites need a gate-closure row.
  STATE_KEYS: Keyword / dict keys whose string values are inventoried.
  REPO_ROOT: Checkout root (parent of ``scripts/``).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_KEYS = frozenset(
  {"status", "outcome", "reason", "kind", "phase", "verify_stage"}
)
GATE_CALLEES = frozenset(
  {
    "requeue_job",
    "_requeue_claimed_job_without_attempt_bump",
    "_requeue_bounded_without_attempt_bump",
    "ack_job",
    "_retry_or_dead_letter",
    "_dead_letter_claim",
    "_fail_append_batch",
    "append_queue_dead_letter",
    "upsert_queue_dead_letter",
    "set_day_skip",
    "set_archive_day_ingest_skip",
    "set_degraded",
    "set_ingest_tar_hot",
    "quarantine_ingest_failed_raw_path",
    "requeue_populate_queue_job",
  }
)


def _callee_name(node: ast.Call) -> str:
  """
  Return the bare function name of a call (``a.b.c()`` -> ``c``).

  Args:
    node (ast.Call): Call node.

  Returns:
    str: Name, or empty when not a simple name/attribute.

  Examples:
    >>> _callee_name(ast.parse("jq.ack_job(x)").body[0].value)
    'ack_job'
  """
  func = node.func
  if isinstance(func, ast.Attribute):
    return func.attr
  if isinstance(func, ast.Name):
    return func.id
  return ""


class _Visitor(ast.NodeVisitor):
  """
  Collect inventory rows for one module (``ast.NodeVisitor`` subclass).

  The base class dispatches ``visit_<Node>``; this subclass tracks the
  enclosing function name and records values, keys and gate invocations.

  Attributes:
    rel (str): Repository-relative module path.
    func (list[str]): Enclosing function name stack.
    out (dict[str, Any]): Shared inventory accumulator.
  """

  def __init__(self, rel: str, out: dict[str, Any]) -> None:
    """
    Bind the module path and accumulator.

    Args:
      rel (str): Repository-relative module path.
      out (dict[str, Any]): Inventory accumulator.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).rel
      'x.py'
    """
    self.rel = rel
    self.func: list[str] = ["<module>"]
    self.out = out

  def _where(self, node: ast.AST) -> str:
    """
    Return ``file:function:line`` for a node.

    Args:
      node (ast.AST): Node with a line number.

    Returns:
      str: Location string.

    Examples:
      >>> _Visitor("x.py", {})._where(ast.parse("1").body[0])
      'x.py:<module>:1'
    """
    return f"{self.rel}:{self.func[-1]}:{getattr(node, 'lineno', 0)}"

  def _record(self, key: str, value: ast.AST, node: ast.AST) -> None:
    """
    Record one state value (constant) or a dynamic producer.

    Args:
      key (str): State key (``status`` …).
      value (ast.AST): Value expression.
      node (ast.AST): Node used for the location.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {"values": {}, "dynamic": []})._record(
      ...   "status", ast.Constant("ok"), ast.Constant("ok")
      ... )
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
      if value.value:
        self.out["values"].setdefault(key, set()).add(value.value)
      return
    if isinstance(value, ast.Constant):
      return
    self.out["dynamic"].append(
      {"key": key, "where": self._where(node), "expr": ast.unparse(value)[:80]}
    )

  def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
    """
    Track the enclosing function while visiting its body.

    Args:
      node (ast.FunctionDef): Function node.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_FunctionDef  # doctest: +SKIP
    """
    self.func.append(node.name)
    self.generic_visit(node)
    self.func.pop()

  visit_AsyncFunctionDef = visit_FunctionDef

  def visit_Call(self, node: ast.Call) -> None:
    """
    Record state keywords and gate invocations.

    Args:
      node (ast.Call): Call node.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_Call  # doctest: +SKIP
    """
    for kw in node.keywords:
      if kw.arg in STATE_KEYS:
        self._record(kw.arg, kw.value, node)
    name = _callee_name(node)
    if name in GATE_CALLEES:
      key = f"{self.rel}:{self.func[-1]}:{name}"
      self.out["call_sites"].setdefault(key, []).append(node.lineno)
    self.generic_visit(node)

  def visit_Constant(self, node: ast.Constant) -> None:
    """
    Record any ``skipped_*`` string literal as a day-removal status.

    Statuses are mostly passed positionally (``_record_entry(p, z, status,
    reason)``) or yielded in tuples, which keyword scanning cannot see.

    Args:
      node (ast.Constant): Constant node.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_Constant  # doctest: +SKIP
    """
    if (
      isinstance(node.value, str)
      and node.value.startswith("skipped_")
      and " " not in node.value
      and node.value not in ("skipped_count", "skipped_paths")
    ):
      self.out["values"].setdefault("status", set()).add(node.value)

  def visit_Return(self, node: ast.Return) -> None:
    """
    Record string returns of the day_close job as day_close outcomes.

    Args:
      node (ast.Return): Return node.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_Return  # doctest: +SKIP
    """
    if (
      self.func[-1] == "_run_day_close_job"
      and isinstance(node.value, ast.Constant)
      and isinstance(node.value.value, str)
    ):
      self.out["values"].setdefault("day_close_outcome", set()).add(
        node.value.value
      )
    self.generic_visit(node)

  def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
    """
    Record annotated ``self.x: T = ...`` fields of ``_DayShard``.

    Args:
      node (ast.AnnAssign): Annotated assignment.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_AnnAssign  # doctest: +SKIP
    """
    if (
      isinstance(node.target, ast.Attribute)
      and self.out.get("_class") == "_DayShard"
    ):
      self.out["shard_fields"].add(node.target.attr)
    self.generic_visit(node)

  def visit_Dict(self, node: ast.Dict) -> None:
    """
    Record ``{"status": "..."}`` style literals.

    Args:
      node (ast.Dict): Dict literal.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_Dict  # doctest: +SKIP
    """
    for k, v in zip(node.keys, node.values, strict=True):
      if isinstance(k, ast.Constant) and k.value in STATE_KEYS:
        self._record(str(k.value), v, node)
    self.generic_visit(node)

  def visit_Assign(self, node: ast.Assign) -> None:
    """
    Record ``x["status"] = ...``, manifest keys and ``_DayShard`` fields.

    Args:
      node (ast.Assign): Assignment.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_Assign  # doctest: +SKIP
    """
    for target in node.targets:
      if (
        isinstance(target, ast.Name)
        and self.func[-1] == "<module>"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
      ):
        for prefix, domain in (
          ("PHASE_", "phase"),
          ("VERIFY_STAGE_", "verify_stage"),
          ("SKIP_KIND_", "day_skip_kind"),
        ):
          if target.id.startswith(prefix):
            self.out["values"].setdefault(domain, set()).add(node.value.value)
      if isinstance(target, ast.Subscript) and isinstance(
        target.slice, ast.Constant
      ):
        key = target.slice.value
        if key in STATE_KEYS:
          self._record(str(key), node.value, node)
        base = ast.unparse(target.value)
        if base.endswith("_manifest") and isinstance(key, str):
          self.out["manifest_keys"].add(key)
      if (
        isinstance(target, ast.Attribute)
        and self.func[-1] == "__init__"
        and self.out.get("_class") == "_DayShard"
      ):
        self.out["shard_fields"].add(target.attr)
    self.generic_visit(node)

  def visit_ClassDef(self, node: ast.ClassDef) -> None:
    """
    Track the class name (``_DayShard`` fields are inventoried).

    Args:
      node (ast.ClassDef): Class node.

    Returns:
      None

    Examples:
      >>> _Visitor("x.py", {}).visit_ClassDef  # doctest: +SKIP
    """
    prior = self.out.get("_class")
    self.out["_class"] = node.name
    if node.name == "_DayShard":
      for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(
          stmt.target, ast.Name
        ):
          self.out["shard_fields"].add(stmt.target.id)
    self.generic_visit(node)
    self.out["_class"] = prior


def build_inventory(root: str = REPO_ROOT) -> dict[str, Any]:
  """
  Build the inventory for ``{root}/hpcperfstats/dbload``.

  Args:
    root (str): Checkout root.

  Returns:
    dict[str, Any]: JSON-ready inventory (sets become sorted lists).

  Examples:
    >>> sorted(build_inventory())[:2]  # doctest: +SKIP
    ['call_sites', 'dynamic']
  """
  out: dict[str, Any] = {
    "values": {},
    "dynamic": [],
    "manifest_keys": set(),
    "shard_fields": set(),
    "call_sites": {},
  }
  base = os.path.join(root, "hpcperfstats", "dbload")
  for dirpath, dirnames, filenames in os.walk(base):
    dirnames[:] = [d for d in dirnames if d not in ("codon", "__pycache__")]
    for name in sorted(filenames):
      if not name.endswith(".py"):
        continue
      path = os.path.join(dirpath, name)
      rel = os.path.relpath(path, root)
      with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=rel)
      _Visitor(rel, out).visit(tree)
  sys.path.insert(0, root)
  from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    PERSISTENCE_ARTIFACT_REGISTRY,
  )

  out.pop("_class", None)
  return {
    "values": {k: sorted(v) for k, v in sorted(out["values"].items())},
    "dynamic": out["dynamic"],
    "manifest_keys": sorted(out["manifest_keys"]),
    "shard_fields": sorted(out["shard_fields"]),
    "registry_kinds": sorted(PERSISTENCE_ARTIFACT_REGISTRY),
    "call_sites": dict(sorted(out["call_sites"].items())),
  }


DYNAMIC_BEGIN = "<!-- BEGIN GENERATED: dynamic producers -->"
DYNAMIC_END = "<!-- END GENERATED: dynamic producers -->"


def render_dynamic_appendix(inv: dict[str, Any]) -> str:
  """
  Render the dynamic-producer appendix block for the state doc.

  Each non-constant ``status``/``outcome``/``reason``/``kind``/``phase`` value
  is listed once per ``file:function:key`` with its expression, so a new
  computed producer changes the block and fails the drift guard until the
  doc is regenerated and reviewed.

  Args:
    inv (dict[str, Any]): ``build_inventory`` output.

  Returns:
    str: Markdown block including the BEGIN/END markers.

  Examples:
    >>> render_dynamic_appendix({"dynamic": []}).splitlines()[0]
    '<!-- BEGIN GENERATED: dynamic producers -->'
  """
  rows: dict[str, set[str]] = {}
  for item in inv["dynamic"]:
    file_, func, _line = item["where"].rsplit(":", 2)
    key = f"`{file_.split('/')[-1]}:{func}` {item['key']}"
    rows.setdefault(key, set()).add(item["expr"].replace("|", "\\|"))
  lines = [DYNAMIC_BEGIN, "", "| Site | Expressions |", "|---|---|"]
  for key in sorted(rows):
    exprs = "; ".join(f"`{e}`" for e in sorted(rows[key]))
    lines.append(f"| {key} | {exprs} |")
  lines += ["", DYNAMIC_END]
  return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
  """
  CLI: write the inventory JSON (default ``../test_runs/``).

  Args:
    argv (list[str] | None): Arguments (``--out PATH``).

  Returns:
    int: Process exit code.

  Examples:
    >>> main(["--out", "/dev/null"])  # doctest: +SKIP
    0
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
  parser.add_argument(
    "--out",
    default=os.path.join(
      os.path.dirname(REPO_ROOT),
      "test_runs",
      "sync_timedb_state_inventory.json",
    ),
  )
  parser.add_argument(
    "--write-doc",
    action="store_true",
    help="rewrite the generated block in docs/SYNC_TIMEDB_STATE_MACHINE.md",
  )
  args = parser.parse_args(argv)
  inv = build_inventory()
  if args.write_doc:
    doc = os.path.join(REPO_ROOT, "docs", "SYNC_TIMEDB_STATE_MACHINE.md")
    with open(doc, encoding="utf-8") as fh:
      text = fh.read()
    head, _sep, rest = text.partition(DYNAMIC_BEGIN)
    _old, _sep, tail = rest.partition(DYNAMIC_END)
    with open(doc, "w", encoding="utf-8") as fh:
      fh.write(head + render_dynamic_appendix(inv) + tail)
  with open(args.out, "w", encoding="utf-8") as fh:
    json.dump(inv, fh, indent=1, sort_keys=True)
  print(
    f"inventory values={sum(len(v) for v in inv['values'].values())} "
    f"dynamic={len(inv['dynamic'])} call_sites={len(inv['call_sites'])} "
    f"-> {args.out}"
  )
  return 0


if __name__ == "__main__":
  sys.exit(main())
