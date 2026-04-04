"""Tests for sysfs NUMA parsing and node-pair selection."""

from pathlib import Path

from hpcperfstats.numa_topology import (
    NumaNode,
    parse_sysfs_numa,
    select_node_pair,
    should_apply_numa_pinning,
)


def _write_node(root: Path, nid: int, cpulist: str) -> None:
  d = root / f"node{nid}"
  d.mkdir(parents=True)
  (d / "cpulist").write_text(cpulist, encoding="utf-8")


def test_parse_sysfs_numa_empty_when_missing(tmp_path):
  assert parse_sysfs_numa(str(tmp_path / "nope")) == []


def test_parse_sysfs_numa_two_nodes(tmp_path):
  root = tmp_path / "node"
  root.mkdir()
  _write_node(root, 0, "0-3")
  _write_node(root, 1, "4-7")
  nodes = parse_sysfs_numa(str(root))
  assert [n.node_id for n in nodes] == [0, 1]
  assert nodes[0].cpulist == "0-3"
  assert nodes[1].cpulist == "4-7"


def test_select_node_pair_defaults_to_two_largest_and_lower_id_web(tmp_path):
  root = tmp_path / "node"
  root.mkdir()
  _write_node(root, 0, "0-1")
  _write_node(root, 1, "2-5")
  nodes = parse_sysfs_numa(str(root))
  w, p = select_node_pair(nodes)
  # Two largest by CPU count are node1 then node0; web = lower sysfs id -> node 0.
  assert w.node_id == 0 and p.node_id == 1
  w2, p2 = select_node_pair(nodes, web_node=0, pipeline_node=1)
  assert w2.node_id == 0 and p2.node_id == 1


def test_should_apply_numa_pinning_respects_thresholds():
  nodes = [
      NumaNode(0, "0-15"),
      NumaNode(1, "16-31"),
  ]
  assert not should_apply_numa_pinning(
      effective_cores=16,
      nodes=nodes,
      pin_min_total=32,
      pin_min_per_node=16,
  )
  assert should_apply_numa_pinning(
      effective_cores=32,
      nodes=nodes,
      pin_min_total=32,
      pin_min_per_node=16,
  )


def test_should_apply_explicit_nodes_bypasses_threshold():
  nodes = [NumaNode(0, "0"), NumaNode(1, "1")]
  assert should_apply_numa_pinning(
      effective_cores=2,
      nodes=nodes,
      pin_min_total=999,
      web_node=0,
      pipeline_node=1,
  )
