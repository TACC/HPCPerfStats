"""Compose-DB oracles for ingest offloads #1-#5 (Phase T gate)."""

from __future__ import annotations

import pytest

from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pdi
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  HOST_PROC_PEAK_KEYS,
  _nullable_int_max,
  peak_merge_proc_objs_with_existing,
)
from hpcperfstats.site.lib.machine.models import proc_data
from hpcperfstats.tests import db_offload_oracles as oracles


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize(
  "left,right,expected",
  [
    (None, None, None),
    (None, 5, 5),
    (10, 3, 10),
    (0, 7000, 7000),
  ],
)
def test_nullable_int_max_matrix(left, right, expected):
  assert _nullable_int_max(left, right) == expected


@pytest.mark.django_db(databases=[])
def test_peak_row_oracle_merge_non_peak_last_write():
  earlier = oracles.proc_row_dict(vm_stk=100, vm_peak=900, threads=1)
  later = oracles.proc_row_dict(vm_stk=50, vm_peak=800, threads=4)
  merged = oracles.oracle_merge_peak_rows(earlier, later)
  assert merged["vm_stk"] == 100
  assert merged["vm_peak"] == 900
  assert merged["threads"] == 4


def _stage_upsert_uses_sql_peak_merge() -> bool:
  sql = pdi._stage_upsert_sql()
  return "GREATEST(" in sql or "CASE WHEN proc_data." in sql


@pytest.fixture
def _force_copy_proc_insert(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "candidate")


@pytest.mark.django_db(transaction=True)
def test_reference_proc_upsert_peak_matrix_compose(_force_copy_proc_insert):
  oracles.skip_unless_compose_db()
  jid = "910001"
  host = "n-dboff.test"
  proc = "app/1"
  proc_data.objects.filter(jid=jid, host=host).delete()

  seed = oracles.proc_model(
    jid=jid, host=host, proc=proc, vm_peak=5000, vm_hwm=4000, device="d/1"
  )
  pdi.insert_proc_data_batch([seed])

  incoming = oracles.proc_model(
    jid=jid,
    host=host,
    proc=proc,
    vm_peak=3000,
    vm_hwm=7000,
    vm_rss=99,
    device="d/2",
  )
  oracles.peak_merge_then_insert(
    [incoming], insert_fn=pdi.insert_proc_data_batch
  )

  row = proc_data.objects.get(jid=jid, host=host, proc=proc)
  assert row.device == "d/2"
  assert row.vm_peak == 5000
  assert row.vm_hwm == 7000
  assert row.vm_rss == 99


@pytest.mark.django_db(transaction=True)
def test_proc_upsert_without_python_merge_matches_reference(
  _force_copy_proc_insert,
):
  """After SQL peak merge (#1), insert without peak_merge must match reference."""
  if not _stage_upsert_uses_sql_peak_merge():
    pytest.skip("SQL peak merge not enabled in _stage_upsert_sql yet")
  oracles.skip_unless_compose_db()
  jid = "910002"
  host = "n-dboff2.test"
  proc = "app/2"
  proc_data.objects.filter(jid=jid, host=host).delete()

  pdi.insert_proc_data_batch(
    [oracles.proc_model(jid=jid, host=host, proc=proc, vm_peak=100, vm_hwm=50)]
  )
  ref_in = oracles.proc_model(
    jid=jid, host=host, proc=proc, vm_peak=80, vm_hwm=200
  )
  peak_merge_proc_objs_with_existing([ref_in])
  pdi.insert_proc_data_batch([ref_in])
  ref_row = proc_data.objects.get(jid=jid, host=host, proc=proc)
  ref_peaks = oracles.peak_fields(ref_row)

  proc_data.objects.filter(jid=jid, host=host, proc=proc).delete()
  pdi.insert_proc_data_batch(
    [oracles.proc_model(jid=jid, host=host, proc=proc, vm_peak=100, vm_hwm=50)]
  )
  prod_in = oracles.proc_model(
    jid=jid, host=host, proc=proc, vm_peak=80, vm_hwm=200
  )
  pdi.insert_proc_data_batch([prod_in])
  prod_row = proc_data.objects.get(jid=jid, host=host, proc=proc)
  assert oracles.peak_fields(prod_row) == ref_peaks
  assert prod_row.device == ref_row.device


@pytest.mark.django_db(transaction=True)
def test_second_presence_sequential_oracle_compose():
  oracles.skip_unless_compose_db()
  from hpcperfstats.site.lib.machine.models import host_data

  host = "n-presence.test"
  present = {1_700_000_100, 1_700_000_105}
  missing = {1_700_000_102}
  host_data.objects.filter(host=host).delete()
  oracles.seed_host_data_seconds(host, present)

  pairs = [(host, s) for s in sorted(present | missing)]
  result = oracles.oracle_sequential_second_presence(pairs)
  assert result[(host, 1_700_000_100)] is True
  assert result[(host, 1_700_000_105)] is True
  assert result[(host, 1_700_000_102)] is False


@pytest.mark.django_db(transaction=True)
def test_batch_second_presence_matches_sequential_oracle():
  from hpcperfstats.dbload.lib import sync_timedb_host_itimes as hi

  if not hasattr(hi, "host_timestamp_seconds_present_batch"):
    pytest.skip("host_timestamp_seconds_present_batch not implemented (#3)")
  oracles.skip_unless_compose_db()
  host = "n-batch-presence.test"
  from hpcperfstats.site.lib.machine.models import host_data as hd

  hd.objects.filter(host=host).delete()
  secs = {1_700_001_000, 1_700_001_003, 1_700_001_007}
  oracles.seed_host_data_seconds(host, {1_700_001_000, 1_700_001_007})
  pairs = [(host, s) for s in sorted(secs)]
  hi.reset_host_itimes_caches()
  oracle = oracles.oracle_sequential_second_presence(pairs)
  batched = hi.host_timestamp_seconds_present_batch(pairs)
  assert batched == oracle


@pytest.mark.django_db(databases=[])
def test_stage_upsert_documents_peak_fields_in_update():
  sql = pdi._stage_upsert_sql()
  for field in HOST_PROC_PEAK_KEYS:
    assert field in sql
