"""Discover Linux NUMA topology from sysfs and pick a node pair for web vs pipeline.

Used by ``scripts/apply_compose_numa_pinning.py`` and tests. Does not read ``/proc``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


SYSFS_NODE_ROOT = "/sys/devices/system/node"


@dataclass(frozen=True)
class NumaNode:
  """One NUMA node with kernel ``cpulist`` string."""

  node_id: int
  cpulist: str


def _parse_node_id(name: str) -> Optional[int]:
  m = re.match(r"^node(\d+)$", name)
  if not m:
    return None
  return int(m.group(1))


def _read_cpulist(node_path: str) -> Optional[str]:
  cpulist_path = os.path.join(node_path, "cpulist")
  try:
    with open(cpulist_path, "r", encoding="utf-8") as f:
      return f.read().strip()
  except OSError:
    return None


def parse_sysfs_numa(sysfs_root: str = SYSFS_NODE_ROOT) -> List[NumaNode]:
  """Return sorted NUMA nodes (by ``node_id``) with ``cpulist`` from sysfs.

  Returns an empty list if ``sysfs_root`` is missing or unreadable.
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
  """Approximate logical CPU count from a kernel cpulist (ranges and lists)."""
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
  return {n.node_id: _cpulist_cpu_count(n.cpulist) for n in nodes}


def select_node_pair(
    nodes: List[NumaNode],
    web_node: Optional[int] = None,
    pipeline_node: Optional[int] = None,
) -> Optional[Tuple[NumaNode, NumaNode]]:
  """Return ``(web_node, pipeline_node)`` or None if not enough nodes.

  If ``web_node`` / ``pipeline_node`` are set, they must exist in ``nodes``.
  Otherwise pick the two nodes with the largest CPU counts (tie: lower id first
  for web).
  """
  if len(nodes) < 2:
    return None
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
  """Return True if compose CPU pinning should be emitted."""
  n = len(nodes)
  if n < 2:
    return False
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
