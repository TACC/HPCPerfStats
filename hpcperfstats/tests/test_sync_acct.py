"""Unit tests for sync_acct ingest logic."""
from __future__ import annotations

import io
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError
from django.test import override_settings

pytestmark = pytest.mark.django_db(databases=[])


SACCT_HEADER = (
    "JobID|User|Account|Start|End|Submit|Partition|Timelimit|JobName|State|"
    "NNodes|ReqCPUS|NodeList"
)


def _sacct_row(
    jid="100",
    user="alice",
    queue="batch",
    start="2024-06-01T10:00:00",
    end="2024-06-01T11:00:00",
    submit="2024-06-01T09:00:00",
    nodes="1",
    cpus="32",
    nodelist="node1",
):
  return (
      f"{jid}|{user}|acct1|{start}|{end}|{submit}|{queue}|01:00:00|job1|"
      f"COMPLETED|{nodes}|{cpus}|{nodelist}"
  )


def test_sync_acct_from_content_empty_returns_zero():
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  assert sync_acct_from_content("", set()) == 0
  assert sync_acct_from_content("   \n", set()) == 0


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_skips_existing_jids(mock_jd, mock_notify):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = SACCT_HEADER + "\n" + _sacct_row(jid="999") + "\n"
  before_qs = MagicMock()
  before_qs.values_list.return_value = []
  after_qs = MagicMock()
  after_qs.values_list.return_value = []
  mock_jd.objects.filter.return_value = before_qs
  before_qs.values_list.side_effect = [[], []]

  with patch.object(
      mock_jd.objects, "bulk_create", return_value=None
  ) as bulk:
    inserted = sync_acct_from_content(content, jobs_in_db={999, "999"})

  assert inserted == 0
  bulk.assert_not_called()
  mock_notify.assert_not_called()


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_bulk_insert_success(mock_jd, mock_notify):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = SACCT_HEADER + "\n" + _sacct_row(jid="501") + "\n"
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["501"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1
  mock_jd.objects.bulk_create.assert_called_once()
  mock_notify.assert_called_once()


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct._insert_job_data_individually", return_value=(1, []))
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_bulk_fallback(mock_jd, mock_fallback, mock_notify):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = SACCT_HEADER + "\n" + _sacct_row(jid="502") + "\n"
  filter_qs = MagicMock()
  filter_qs.values_list.return_value = []
  mock_jd.objects.filter.return_value = filter_qs
  mock_jd.objects.bulk_create.side_effect = RuntimeError("bulk failed")

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1
  mock_fallback.assert_called_once()
  mock_notify.assert_called_once()


@override_settings(DEBUG=True)
@patch("hpcperfstats.dbload.sync_acct.cfg.get_restricted_queue_keywords", return_value=["secret"])
@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_filters_restricted_queue(mock_jd, _notify, _keywords):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = (
      SACCT_HEADER + "\n"
      + _sacct_row(jid="601", queue="secret-batch") + "\n"
      + _sacct_row(jid="602", queue="batch") + "\n"
  )
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["602"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1
  created = mock_jd.objects.bulk_create.call_args[0][0]
  assert len(created) == 1
  assert created[0].jid == "602"


@patch("hpcperfstats.site.machine.cache_utils.warm_job_cache_entries")
@patch("hpcperfstats.site.machine.cache_utils.invalidate_after_job_data_ingest")
def test_notify_job_cache_after_acct_ingest_warms(mock_inv, mock_warm):
  from hpcperfstats.dbload.sync_acct import _notify_job_cache_after_acct_ingest

  obj = MagicMock(jid="777")
  _notify_job_cache_after_acct_ingest(1, [obj], inserted_jids=["777"])
  mock_inv.assert_called_once()
  mock_warm.assert_called_once()


@patch("hpcperfstats.dbload.sync_acct.job_data_instance_from_acct_row")
def test_insert_job_data_individually_skips_integrity_error(mock_from_row):
  from hpcperfstats.dbload.sync_acct import _insert_job_data_individually
  import pandas as pd

  row = MagicMock(jid="900")
  df = pd.DataFrame([{"jid": "900"}])
  obj = MagicMock()
  obj.save.side_effect = IntegrityError()
  mock_from_row.return_value = obj

  inserted, saved = _insert_job_data_individually(df)

  assert inserted == 0
  assert saved == []


def _sacct_content(*rows):
  lines = [SACCT_HEADER] + list(rows)
  return "\n".join(lines) + "\n"


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_creates_file(mock_acct_path, tmp_path):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  content = _sacct_content(_sacct_row(jid="701"))
  ingest_date = date(2024, 6, 15)

  persist_accounting_daily_file(ingest_date, content)

  path = tmp_path / "2024-06-15.txt"
  assert path.read_text(encoding="utf-8") == content


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_overwrites_when_not_shrinking(mock_acct_path, tmp_path):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)
  path = tmp_path / "2024-06-15.txt"
  original = _sacct_content(_sacct_row(jid="801"), _sacct_row(jid="802"))
  path.write_text(original, encoding="utf-8")
  updated = _sacct_content(_sacct_row(jid="801"), _sacct_row(jid="803"))

  persist_accounting_daily_file(ingest_date, updated)

  assert path.read_text(encoding="utf-8") == updated


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_rejects_shrink(mock_acct_path, tmp_path):
  from hpcperfstats.dbload.sync_acct import (
      AccountingFileShrinkError,
      persist_accounting_daily_file,
  )

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)
  path = tmp_path / "2024-06-15.txt"
  original = _sacct_content(_sacct_row(jid="901"), _sacct_row(jid="902"))
  path.write_text(original, encoding="utf-8")
  shorter = _sacct_content(_sacct_row(jid="901"))

  with pytest.raises(AccountingFileShrinkError) as exc_info:
    persist_accounting_daily_file(ingest_date, shorter)

  assert exc_info.value.existing_lines == 3
  assert exc_info.value.incoming_lines == 2
  assert path.read_text(encoding="utf-8") == original


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persisted_file_is_reingestible_by_sync_acct(
    mock_acct_path, mock_jd, _notify, tmp_path,
):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file, sync_acct

  mock_acct_path.return_value = str(tmp_path)
  content = _sacct_content(_sacct_row(jid="1001"))
  ingest_date = date(2024, 6, 15)
  persist_accounting_daily_file(ingest_date, content)

  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["1001"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct(str(tmp_path / "2024-06-15.txt"), set())

  assert inserted == 1
  mock_jd.objects.bulk_create.assert_called_once()
