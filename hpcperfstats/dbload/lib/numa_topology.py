"""
Discover Linux NUMA topology from sysfs and pick cpusets for web vs pipeline.

With **two or more** nodes, web and pipeline use **different** node cpusets.
With **one** node, both services share that node's ``cpulist`` (still useful for
explicit compose ``cpuset`` on single-socket hosts that expose ``node0``).

Used by ``scripts/apply_compose_cpu_pinning.py`` and tests. Does not read
``/proc``.

Attributes:
  SYSFS_NODE_ROOT: Attribute.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


SYSFS_NODE_ROOT = "/sys/devices/system/node"


@dataclass(frozen=True)
class NumaNode:
  """
  One NUMA node with kernel ``cpulist`` string.
  
  Attributes:
    cpulist: Attribute.
    node_id: Attribute.
  """

  node_id: int
  cpulist: str


def _parse_node_id(name: str) -> Optional[int]:
  """
  Internal helper to parse the node id.
  
  Args:
    name (str): String for name.
  
  Returns:
    Optional[int]: Optional[int] — the result, or None when unavailable.
  
  Examples:
    >>> _parse_node_id("x")  # doctest: +SKIP
  """
  m = re.match(r"^node(\d+)$", name)
  if not m:
    return None
  return int(m.group(1))


def _read_cpulist(node_path: str) -> Optional[str]:
  """
  Internal helper to read the cpulist.
  
  Args:
    node_path (str): String for node path.
  
  Returns:
    Optional[str]: Optional[str] — the result, or None when unavailable.
  
  Examples:
    >>> _read_cpulist("x")  # doctest: +SKIP
  """
  cpulist_path = os.path.join(node_path, "cpulist")
  try:
    with open(cpulist_path, "r", encoding="utf-8") as f:
      return f.read().strip()
  except OSError:
    return None


def parse_sysfs_numa(sysfs_root: str = SYSFS_NODE_ROOT) -> List[NumaNode]:
  """
  Return sorted NUMA nodes (by ``node_id``) with ``cpulist`` from sysfs.
  
  Returns an empty list if ``sysfs_root`` is missing or unreadable.
  
  Args:
    sysfs_root (str): String for sysfs root.
  
  Returns:
    List[NumaNode]: List[NumaNode] produced by this call.
  
  Examples:
    >>> parse_sysfs_numa("x")  # doctest: +SKIP
  """
  if not os.path.isdir(sysfs_root):
    return []
  nodes: List[NumaNode] = []
  try:
    entries = os.listdir(sysfs_root)
  except OSError:
    return []
  for name in entries:
    nid = _parse_node_id(name)
    if nid is None:
      continue
    path = os.path.join(sysfs_root, name)
    if not os.path.isdir(path):
      continue
    cl = _read_cpulist(path)
    if not cl:
      continue
    nodes.append(NumaNode(node_id=nid, cpulist=cl))
  nodes.sort(key=lambda n: n.node_id)
  return nodes


def _cpulist_cpu_count(cpulist: str) -> int:
  """
  Approximate logical CPU count from a kernel cpulist (ranges and lists).
  
  Args:
    cpulist (str): String for cpulist.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> _cpulist_cpu_count("x")  # doctest: +SKIP
  """
  total = 0
  for part in cpulist.split(","):
    part = part.strip()
    if not part:
      continue
    if "-" in part:
      a, b = part.split("-", 1)
      try:
        lo, hi = int(a.strip()), int(b.strip())
        total += max(0, hi - lo + 1)
      except ValueError:
        continue
    else:
      try:
        int(part)
        total += 1
      except ValueError:
        continue
  return total


def _node_cpu_counts(nodes: List[NumaNode]) -> Dict[int, int]:
  """
  Internal helper to handle node cpu counts.
  
  Args:
    nodes (List[NumaNode]): Sequence for nodes.
  
  Returns:
    Dict[int, int]: Dict[int, int] produced by this call.
  
  Examples:
    >>> _node_cpu_counts([])  # doctest: +SKIP
  """
  return {n.node_id: _cpulist_cpu_count(n.cpulist) for n in nodes}


def select_node_pair(
  nodes: List[NumaNode],
  web_node: Optional[int] = None,
  pipeline_node: Optional[int] = None,
) -> Optional[Tuple[NumaNode, NumaNode]]:
  """
  Return ``(web_node, pipeline_node)`` NumaNode tuple or None.
  
  **Single NUMA node:** returns ``(node0, node0)`` — web and pipeline share the
  same ``cpulist`` (no cross-node isolation, but compose cpusets are explicit).
  
  **Two or more nodes:** if ``web_node`` / ``pipeline_node`` are set, they must
  exist and differ. Otherwise pick the two nodes with the largest CPU counts
  (tie: lower id first for web).
  
  Args:
    nodes (List[NumaNode]): Sequence for nodes.
    web_node (Optional[int]): Web node, or None when absent.
    pipeline_node (Optional[int]): Pipeline node, or None when absent.
  
  Returns:
    Optional[Tuple[NumaNode, NumaNode]]: Optional[Tuple[NumaNode, NumaNode]] —
    the result, or None when unavailable.
  
  Examples:
    >>> select_node_pair([], None, None)  # doctest: +SKIP
  """
  if not nodes:
    return None
  if len(nodes) == 1:
    only = nodes[0]
    if web_node is not None or pipeline_node is not None:
      if web_node is None or pipeline_node is None:
        return None
      if web_node != pipeline_node or web_node != only.node_id:
        return None
    return (only, only)
  by_id = {n.node_id: n for n in nodes}
  if web_node is not None and pipeline_node is not None:
    wn = by_id.get(web_node)
    pn = by_id.get(pipeline_node)
    if wn is None or pn is None or web_node == pipeline_node:
      return None
    return (wn, pn)
  counts = _node_cpu_counts(nodes)
  ranked = sorted(
      nodes,
      key=lambda n: (-counts[n.node_id], n.node_id),
  )
  hi_a, hi_b = ranked[0], ranked[1]
  # Lower sysfs node id -> web (stable policy among the two largest CPU-count nodes).
  if hi_a.node_id <= hi_b.node_id:
    return (hi_a, hi_b)
  return (hi_b, hi_a)


def should_apply_numa_pinning(
  effective_cores: int,
  nodes: List[NumaNode],
  pin_min_total: int = 32,
  pin_min_per_node: int = 16,
  max_nodes_auto: int = 16,
  web_node: Optional[int] = None,
  pipeline_node: Optional[int] = None,
) -> bool:
  """
  Return True if compose CPU pinning should be emitted.
  
  Args:
    effective_cores (int): Integer value for effective cores.
    nodes (List[NumaNode]): Sequence for nodes.
    pin_min_total (int): Integer value for pin min total.
    pin_min_per_node (int): Integer value for pin min per node.
    max_nodes_auto (int): Integer value for max nodes auto.
    web_node (Optional[int]): Web node, or None when absent.
    pipeline_node (Optional[int]): Pipeline node, or None when absent.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> should_apply_numa_pinning(0, [], 0, 0, 0, None, None)  # doctest: +SKIP
  """
  n = len(nodes)
  if n < 1:
    return False
  if n == 1:
    only = nodes[0]
    counts = _node_cpu_counts(nodes)
    if web_node is not None and pipeline_node is not None:
      if web_node != pipeline_node or web_node != only.node_id:
        return False
      return True
    if web_node is not None or pipeline_node is not None:
      return False
    if effective_cores < pin_min_total:
      return False
    if counts[only.node_id] < pin_min_per_node:
      return False
    return True
  if web_node is not None and pipeline_node is not None:
    return True
  if n > max_nodes_auto:
    return False
  if effective_cores < pin_min_total:
    return False
  pair = select_node_pair(nodes)
  if pair is None:
    return False
  counts = _node_cpu_counts(nodes)
  for node in pair:
    if counts[node.node_id] < pin_min_per_node:
      return False
  return True
