"""Drift guard: docs/SYNC_TIMEDB_STATE_MACHINE.md covers every inventoried state."""

from __future__ import annotations

import importlib.util
import os

import pytest

ROOT = os.path.dirname(
  os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DOC = os.path.join(ROOT, "docs", "SYNC_TIMEDB_STATE_MACHINE.md")


@pytest.fixture(scope="module")
def inventory_mod():
  spec = importlib.util.spec_from_file_location(
    "sync_timedb_state_inventory",
    os.path.join(ROOT, "scripts", "sync_timedb_state_inventory.py"),
  )
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


@pytest.fixture(scope="module")
def inventory(inventory_mod):
  return inventory_mod.build_inventory(ROOT)


@pytest.fixture(scope="module")
def doc():
  with open(DOC, encoding="utf-8") as fh:
    return fh.read()


def test_state_doc_mentions_every_registry_kind_and_shard_field(inventory, doc):
  from hpcperfstats.dbload.lib import sync_timedb_job_store as jq

  missing = [k for k in inventory["registry_kinds"] if f"`{k}`" not in doc]
  missing += [f for f in inventory["shard_fields"] if f"`{f}`" not in doc]
  missing += [
    name for name in dir(jq) if name.startswith("JOB_KIND_") and name not in doc
  ]
  assert missing == [], missing
  assert inventory["shard_fields"], "positive control: shard fields found"


def test_state_inventory_matches_doc_rows(inventory, inventory_mod, doc):
  domains = (
    "status",
    "phase",
    "verify_stage",
    "day_skip_kind",
    "day_close_outcome",
    "outcome",
  )
  missing = [
    f"{d}:{v}"
    for d in domains
    for v in inventory["values"].get(d, [])
    if f"`{v}`" not in doc
  ]
  assert missing == [], missing
  missing_keys = [k for k in inventory["manifest_keys"] if f"`{k}`" not in doc]
  assert missing_keys == [], missing_keys
  expected = inventory_mod.render_dynamic_appendix(inventory)
  assert expected in doc, (
    "dynamic producers changed: review and run "
    "scripts/sync_timedb_state_inventory.py --write-doc"
  )


def test_every_gate_site_has_closure_row(inventory, doc):
  rows = []
  for key in inventory["call_sites"]:
    file_, func, callee = key.rsplit(":", 2)
    rows.append(f"`{os.path.basename(file_)}:{func}:{callee}`")
  assert len(rows) > 20, "positive control: gate sites inventoried"
  missing = [r for r in rows if r not in doc]
  assert missing == [], missing
  assert "not analysed" not in doc.lower()
