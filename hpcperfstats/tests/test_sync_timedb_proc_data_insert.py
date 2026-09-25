"""Unit tests for proc_data COPY upsert arm routing."""
from __future__ import annotations

from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pdi
from hpcperfstats.site.lib.machine.models import proc_data


def _obj(**overrides):
  base = dict(
      jid="1",
      host="h",
      proc="bash",
      device="bash/1",
      uid=1000,
      vm_peak=1,
      vm_size=1,
      vm_lck=0,
      vm_hwm=1,
      vm_rss=1,
      vm_data=1,
      vm_stk=1,
      vm_exe=1,
      vm_lib=1,
      vm_pte=1,
      vm_swap=0,
      threads=1,
  )
  base.update(overrides)
  return proc_data(**base)


def test_proc_insert_arm_default_candidate_after_retain(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_PROC_INSERT_ARM", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_PROC_DATA_COPY", raising=False)
  assert pdi.proc_insert_arm() == "candidate"


def test_proc_insert_routes_candidate(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "candidate")
  seen = []
  monkeypatch.setattr(pdi, "bulk_insert_proc_data_update_conflicts", lambda o: seen.append(len(o)))
  monkeypatch.setattr(
      pdi,
      "bulk_create_proc_data_update_conflicts",
      lambda _o: (_ for _ in ()).throw(AssertionError("bulk")),
  )
  pdi.insert_proc_data_batch([_obj()])
  assert seen == [1]


def test_proc_copy_sql_has_conflict_update():
  sql = pdi._stage_upsert_sql()
  assert "ON CONFLICT (jid, host, proc) DO UPDATE SET" in sql
  assert "vm_rss = EXCLUDED.vm_rss" in sql


def test_zzz_proc_data_insert_unit_ok_marker():
  print("proc_data_insert_unit_ok")
