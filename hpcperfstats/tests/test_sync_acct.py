"""Unit tests for sync_acct ingest logic."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError
from django.test import override_settings
from pandas.errors import ParserError

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
  timelimit="01:00:00",
):
  return (
    f"{jid}|{user}|acct1|{start}|{end}|{submit}|{queue}|{timelimit}|job1|"
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

  with patch.object(mock_jd.objects, "bulk_create", return_value=None) as bulk:
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
@patch(
  "hpcperfstats.dbload.sync_acct._insert_job_data_individually",
  return_value=(1, []),
)
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_bulk_fallback(
  mock_jd, mock_fallback, mock_notify
):
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
@patch(
  "hpcperfstats.dbload.sync_acct.cfg.get_restricted_queue_keywords",
  return_value=["secret"],
)
@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_filters_restricted_queue(mock_jd, _notify, _keywords):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = (
    SACCT_HEADER
    + "\n"
    + _sacct_row(jid="601", queue="secret-batch")
    + "\n"
    + _sacct_row(jid="602", queue="batch")
    + "\n"
  )
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["602"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1
  created = mock_jd.objects.bulk_create.call_args[0][0]
  assert len(created) == 1
  assert created[0].jid == "602"


@patch(
  "hpcperfstats.site.lib.machine.cache_utils.mark_overlapping_jobs_metrics_dirty",
  return_value=0,
)
@patch("hpcperfstats.site.lib.machine.cache_utils.warm_job_cache_entries")
@patch(
  "hpcperfstats.site.lib.machine.cache_utils.invalidate_after_job_data_ingest"
)
def test_notify_job_cache_after_acct_ingest_warms(
  mock_inv, mock_warm, mock_dirty
):
  from hpcperfstats.dbload.sync_acct import (
    _notify_job_cache_after_acct_ingest,
  )

  obj = MagicMock(jid="777")
  _notify_job_cache_after_acct_ingest(1, [obj], inserted_jids=["777"])
  mock_inv.assert_called_once()
  mock_warm.assert_called_once()
  mock_dirty.assert_called_once_with(["777"])


@patch("hpcperfstats.site.lib.machine.cache_utils.warm_job_cache_entries")
@patch(
  "hpcperfstats.site.lib.machine.cache_utils.invalidate_after_job_data_ingest"
)
def test_notify_overlap_dirty_stamp_failure_is_logged_not_raised(
  _mock_inv, _mock_warm
):
  """horizon-idle-allocation-tagging: a failed overlap stamp must not fail
  accounting ingest, and must be visible in the log."""
  from hpcperfstats.dbload.sync_acct import (
    _notify_job_cache_after_acct_ingest,
  )

  with (
    patch(
      "hpcperfstats.site.lib.machine.cache_utils."
      "mark_overlapping_jobs_metrics_dirty",
      side_effect=RuntimeError("db down"),
    ),
    patch("hpcperfstats.dbload.sync_acct.log_print") as log,
  ):
    _notify_job_cache_after_acct_ingest(1, [], inserted_jids=["777"])
  log.assert_called_once_with(
    "WARNING: overlap metrics-dirty stamp failed:", "RuntimeError: db down"
  )


@patch(
  "hpcperfstats.site.lib.machine.cache_utils.mark_overlapping_jobs_metrics_dirty"
)
@patch(
  "hpcperfstats.site.lib.machine.cache_utils.invalidate_after_job_data_ingest"
)
def test_notify_skips_overlap_stamp_without_inserted_jids(
  _mock_inv, mock_dirty
):
  from hpcperfstats.dbload.sync_acct import (
    _notify_job_cache_after_acct_ingest,
  )

  _notify_job_cache_after_acct_ingest(0, None, inserted_jids=[])
  mock_dirty.assert_not_called()


@patch("hpcperfstats.dbload.sync_acct.job_data_instance_from_acct_row")
def test_insert_job_data_individually_skips_integrity_error(mock_from_row):
  import pandas as pd

  from hpcperfstats.dbload.sync_acct import _insert_job_data_individually

  df = pd.DataFrame([{"jid": "900"}])
  obj = MagicMock()
  obj.save.side_effect = IntegrityError()
  mock_from_row.return_value = obj

  inserted, saved = _insert_job_data_individually(df)

  assert inserted == 0
  assert saved == []


def _sacct_content(*rows):
  lines = [SACCT_HEADER, *list(rows)]
  return "\n".join(lines) + "\n"


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_creates_file(mock_acct_path, tmp_path):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  content = _sacct_content(_sacct_row(jid="701"))
  ingest_date = date(2024, 6, 15)

  wrote = persist_accounting_daily_file(ingest_date, content)

  path = tmp_path / "2024-06-15.txt"
  assert wrote is True
  assert path.read_text(encoding="utf-8") == content


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_overwrites_when_not_shrinking(
  mock_acct_path, tmp_path
):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)
  path = tmp_path / "2024-06-15.txt"
  original = _sacct_content(_sacct_row(jid="801"), _sacct_row(jid="802"))
  path.write_text(original, encoding="utf-8")
  updated = _sacct_content(_sacct_row(jid="801"), _sacct_row(jid="803"))

  wrote = persist_accounting_daily_file(ingest_date, updated)

  assert wrote is True
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


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_skips_empty(mock_acct_path, tmp_path):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)

  wrote = persist_accounting_daily_file(ingest_date, "  \n")

  assert wrote is False
  assert not (tmp_path / "2024-06-15.txt").exists()


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_skips_header_only(
  mock_acct_path, tmp_path
):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)

  wrote = persist_accounting_daily_file(ingest_date, "JobID|User\n\n")

  assert wrote is False
  assert not (tmp_path / "2024-06-15.txt").exists()


@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persist_accounting_daily_file_skip_does_not_replace_existing(
  mock_acct_path,
  tmp_path,
):
  from hpcperfstats.dbload.sync_acct import persist_accounting_daily_file

  mock_acct_path.return_value = str(tmp_path)
  ingest_date = date(2024, 6, 15)
  path = tmp_path / "2024-06-15.txt"
  original = _sacct_content(_sacct_row(jid="901"), _sacct_row(jid="902"))
  path.write_text(original, encoding="utf-8")

  wrote = persist_accounting_daily_file(ingest_date, "JobID|User\n")

  assert wrote is False
  assert path.read_text(encoding="utf-8") == original


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
@patch("hpcperfstats.dbload.sync_acct.cfg.get_accounting_path")
def test_persisted_file_is_reingestible_by_sync_acct(
  mock_acct_path,
  mock_jd,
  _notify,
  tmp_path,
):
  from hpcperfstats.dbload.sync_acct import (
    persist_accounting_daily_file,
    sync_acct,
  )

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


def test_acct_timelimit_to_seconds_day_and_hhmmss():
  import pandas as pd

  from hpcperfstats.dbload.sync_acct import _acct_timelimit_to_seconds

  out = _acct_timelimit_to_seconds(pd.Series(["01:00:00", "1-02:00:00"]))
  assert list(out) == [3600.0, 93600.0]


def test_acct_timelimit_to_seconds_sentinels_and_garbage():
  import math

  import pandas as pd

  from hpcperfstats.dbload.sync_acct import _acct_timelimit_to_seconds

  out = _acct_timelimit_to_seconds(
    pd.Series(
      [
        "UNLIMITED",
        "Partition_Limit",
        "Partition_limit",
        " INFINITE ",
        "INVALID",
        "not-a-duration",
        "",
      ]
    ),
  )
  assert all(
    pd.isna(v) or (isinstance(v, float) and math.isnan(v)) for v in out
  )


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_unlimited_timelimit_does_not_abort_batch(
  mock_jd,
  mock_notify,
):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = _sacct_content(
    _sacct_row(jid="1101", timelimit="UNLIMITED"),
    _sacct_row(jid="1102", timelimit="01:00:00"),
  )
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["1101", "1102"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 2
  created = mock_jd.objects.bulk_create.call_args[0][0]
  by_jid = {obj.jid: obj for obj in created}
  assert by_jid["1101"].timelimit is None
  assert by_jid["1102"].timelimit == 3600.0
  mock_notify.assert_called_once()


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_from_content_partition_limit_timelimit(mock_jd, mock_notify):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = _sacct_content(
    _sacct_row(jid="1201", timelimit="Partition_Limit"),
    _sacct_row(jid="1202", timelimit="Partition_limit"),
    _sacct_row(jid="1203", timelimit="1-02:00:00"),
  )
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["1201", "1202", "1203"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 3
  created = mock_jd.objects.bulk_create.call_args[0][0]
  by_jid = {obj.jid: obj for obj in created}
  assert by_jid["1201"].timelimit is None
  assert by_jid["1202"].timelimit is None
  assert by_jid["1203"].timelimit == 93600.0
  mock_notify.assert_called_once()


def _assert_load_reason(content, *needles):
  from hpcperfstats.dbload.sync_acct import (
    AccountingFileLoadError,
    sync_acct_from_content,
  )

  with pytest.raises(AccountingFileLoadError) as exc_info:
    sync_acct_from_content(content, set())
  reason = exc_info.value.reason
  for needle in needles:
    assert needle in reason, reason
  return reason


def _header_and_row_without(column, **row_kwargs):
  header_names = SACCT_HEADER.split("|")
  row_names = _sacct_row(**row_kwargs).split("|")
  index = header_names.index(column)
  del header_names[index]
  del row_names[index]
  return "|".join(header_names) + "\n" + "|".join(row_names) + "\n"


def test_sync_acct_from_content_missing_column_names_reason():
  content = _header_and_row_without("JobID")
  _assert_load_reason(content, "missing required sacct columns", "JobID")


def test_sync_acct_from_content_bad_nodelist_names_jid():
  content = _sacct_content(_sacct_row(jid="8801", nodelist="c[1"))
  _assert_load_reason(content, "NodeList", "8801", "unbalanced brackets")


def test_sync_acct_from_content_non_text_nodelist_names_jid():
  content = _sacct_content(_sacct_row(jid="8802", nodelist=""))
  _assert_load_reason(content, "NodeList", "8802")


def test_sync_acct_from_content_bad_timestamp_reason():
  content = _sacct_content(_sacct_row(jid="8803", start="not-a-time"))
  _assert_load_reason(content, "could not parse Start, End, or Submit")


@override_settings(DEBUG=True)
@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
@patch(
  "hpcperfstats.dbload.sync_acct.cfg.get_restricted_queue_keywords",
  return_value=["secret"],
)
def test_sync_acct_restricted_queue_nan_partition_still_inserts(
  _keywords, mock_jd, _notify
):
  """Empty Partition (pandas NaN) must not abort ingest when keywords are set."""
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = _sacct_content(_sacct_row(jid="8804", queue=""))
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["8804"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1
  created = mock_jd.objects.bulk_create.call_args[0][0]
  assert len(created) == 1
  assert created[0].jid == "8804"
  assert created[0].queue is None


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
@patch(
  "hpcperfstats.dbload.sync_acct.cfg.get_restricted_queue_keywords",
  return_value=[],
)
def test_sync_acct_empty_keywords_allow_non_text_queue(
  _keywords, mock_jd, _notify
):
  from hpcperfstats.dbload.sync_acct import sync_acct_from_content

  content = _sacct_content(_sacct_row(jid="8805", queue=""))
  filter_qs = MagicMock()
  filter_qs.values_list.side_effect = [[], ["8805"]]
  mock_jd.objects.filter.return_value = filter_qs

  inserted = sync_acct_from_content(content, jobs_in_db=set())

  assert inserted == 1


def test_sync_acct_oserror_reason(tmp_path):
  from hpcperfstats.dbload.sync_acct import (
    AccountingFileLoadError,
    sync_acct,
  )

  missing = tmp_path / "2024-06-01.txt"
  with pytest.raises(AccountingFileLoadError) as exc_info:
    sync_acct(str(missing), set())
  reason = exc_info.value.reason
  assert "cannot read accounting file" in reason
  assert "errno" in reason


@patch(
  "hpcperfstats.dbload.sync_acct.file_read_lock_wait",
  side_effect=TimeoutError(
    "Timed out waiting for read lock: /tmp/acct.fnctl.lock"
  ),
)
def test_sync_acct_lock_timeout_reason(_lock):
  from hpcperfstats.dbload.sync_acct import (
    AccountingFileLoadError,
    sync_acct,
  )

  with pytest.raises(AccountingFileLoadError) as exc_info:
    sync_acct("/tmp/2024-06-01.txt", set())
  assert "timed out waiting for read lock" in exc_info.value.reason.lower()


def test_sync_acct_load_reason_backstop_includes_exception_type():
  from hpcperfstats.dbload.sync_acct import (
    AccountingFileLoadError,
    _acct_load_skip_reason,
  )

  plain = _acct_load_skip_reason(RuntimeError("disk blew up"))
  assert plain == "RuntimeError: disk blew up"
  named = AccountingFileLoadError("missing required sacct columns: JobID")
  assert _acct_load_skip_reason(named) == named.reason
  assert not _acct_load_skip_reason(named).startswith("AccountingFileLoadError")


def test_sync_acct_from_content_empty_data_reason():
  _assert_load_reason('"', "sacct file has no header columns")


@patch(
  "hpcperfstats.dbload.sync_acct.read_csv",
  side_effect=ParserError("tokenizing data"),
)
def test_sync_acct_from_content_parser_error_reason(_read):
  _assert_load_reason(
    "JobID|User\n1|alice\n",
    "pipe-delimited sacct parse failed",
  )


def test_sync_acct_from_content_node_hours_type_error_reason():
  content = _sacct_content(_sacct_row(jid="8806", nodes="abc"))
  _assert_load_reason(
    content, "could not compute node hours from NNodes and runtime"
  )


@patch(
  "hpcperfstats.dbload.sync_acct.job_data_instance_from_acct_row",
  side_effect=AttributeError("bad field"),
)
def test_sync_acct_from_content_job_row_build_reason(_build):
  content = _sacct_content(_sacct_row(jid="8807"))
  _assert_load_reason(
    content, "could not build job row jid=8807", "AttributeError"
  )


@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_database_error_before_insert_reason(mock_jd):
  mock_jd.objects.filter.side_effect = RuntimeError("connection lost")
  content = _sacct_content(_sacct_row(jid="8808"))
  _assert_load_reason(
    content,
    "database error checking existing jobs",
    "RuntimeError",
    "connection lost",
  )


@patch("hpcperfstats.dbload.sync_acct._notify_job_cache_after_acct_ingest")
@patch("hpcperfstats.dbload.sync_acct.job_data")
def test_sync_acct_database_error_after_bulk_insert_reason(mock_jd, _notify):
  calls = {"n": 0}

  def _filter(*_args, **_kwargs):
    calls["n"] += 1
    query = MagicMock()
    if calls["n"] == 1:
      query.values_list.return_value = []
    else:
      query.values_list.side_effect = RuntimeError("count failed")
    return query

  mock_jd.objects.filter.side_effect = _filter
  content = _sacct_content(_sacct_row(jid="8809"))
  _assert_load_reason(
    content,
    "database error counting inserted jobs after bulk insert",
    "rows may already be inserted",
    "RuntimeError",
  )


@override_settings(DEBUG=True)
@patch("hpcperfstats.dbload.sync_acct.log_print")
@patch(
  "hpcperfstats.dbload.sync_acct.sync_acct",
  side_effect=RuntimeError("boom"),
)
def test_sync_acct_debug_logs_reason_then_reraises(_sync, mock_log):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  with pytest.raises(RuntimeError, match="boom"):
    _load_acct_file_for_daemon("/tmp/2024-06-01.txt", set())
  logged = mock_log.call_args[0][0]
  assert logged == (
    "Unable to load file: /tmp/2024-06-01.txt: RuntimeError: boom"
  )


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
@patch(
  "hpcperfstats.dbload.sync_acct.sync_acct",
  side_effect=RuntimeError("boom"),
)
def test_sync_acct_non_debug_logs_reason_and_continues(_sync, mock_log):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  _load_acct_file_for_daemon("/tmp/2024-06-01.txt", set())
  logged = mock_log.call_args[0][0]
  assert logged == (
    "Unable to load file: /tmp/2024-06-01.txt: RuntimeError: boom"
  )


def _dead_letter_logs(mock_log):
  return [call.args[0] for call in mock_log.call_args_list]


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_flag_off_leaves_bad_file(mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  body = _header_and_row_without("JobID")
  path.write_text(body)
  _load_acct_file_for_daemon(str(path), set(), dead_letter=False)
  assert path.read_text() == body
  assert not (tmp_path / ".unimportable-joblist").exists()
  assert any(
    "Unable to load file:" in line for line in _dead_letter_logs(mock_log)
  )
  assert not any(
    "Moved unimportable accounting file" in line
    for line in _dead_letter_logs(mock_log)
  )


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_moves_unimportable_payload(mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  body = _header_and_row_without("JobID")
  path.write_text(body)
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  dest = tmp_path / ".unimportable-joblist" / "2024-06-01.txt"
  assert not path.exists()
  assert dest.read_text() == body
  assert any(
    f"Moved unimportable accounting file to {dest}" in line
    for line in _dead_letter_logs(mock_log)
  )


@override_settings(DEBUG=False)
@patch(
  "hpcperfstats.dbload.sync_acct.file_read_lock_wait",
  side_effect=PermissionError(13, "Permission denied"),
)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_moves_unreadable_file(_lock, mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  path.write_text("still here")
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  dest = tmp_path / ".unimportable-joblist" / "2024-06-01.txt"
  assert not path.exists()
  assert dest.read_text() == "still here"


@override_settings(DEBUG=False)
@patch(
  "hpcperfstats.dbload.sync_acct.file_read_lock_wait",
  side_effect=TimeoutError("Timed out waiting for read lock: acct.fnctl.lock"),
)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_leaves_lock_timeout(_lock, mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  path.write_text("locked")
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  assert path.read_text() == "locked"
  assert not (tmp_path / ".unimportable-joblist").exists()


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.job_data")
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_leaves_database_error(mock_log, mock_jd, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  body = _sacct_content(_sacct_row(jid="8810"))
  path.write_text(body)
  mock_jd.objects.filter.side_effect = RuntimeError("connection lost")
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  assert path.read_text() == body
  assert not (tmp_path / ".unimportable-joblist").exists()
  assert any("database error" in line for line in _dead_letter_logs(mock_log))


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_missing_file_does_not_raise(mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  assert not path.exists()
  assert not (tmp_path / ".unimportable-joblist").exists()
  assert any(
    "Unable to load file:" in line for line in _dead_letter_logs(mock_log)
  )


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.time.time", return_value=1700000000)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_does_not_clobber_existing(mock_log, _time, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  body = _header_and_row_without("JobID")
  path.write_text(body)
  box = tmp_path / ".unimportable-joblist"
  box.mkdir()
  kept = box / "2024-06-01.txt"
  kept.write_text("KEEP")
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  assert kept.read_text() == "KEEP"
  assert not path.exists()
  moved = box / "2024-06-01.txt.1700000000"
  assert moved.read_text() == body


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.time.time", return_value=1700000000)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_refuses_when_suffix_taken(mock_log, _time, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  body = _header_and_row_without("JobID")
  path.write_text(body)
  box = tmp_path / ".unimportable-joblist"
  box.mkdir()
  (box / "2024-06-01.txt").write_text("KEEP")
  (box / "2024-06-01.txt.1700000000").write_text("ALSO")
  _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  assert path.read_text() == body
  assert (box / "2024-06-01.txt").read_text() == "KEEP"
  assert (box / "2024-06-01.txt.1700000000").read_text() == "ALSO"
  assert any(
    line.startswith("Could not move unimportable accounting file ")
    for line in _dead_letter_logs(mock_log)
  )


def test_dead_letter_flag_stripped_before_dates():
  from datetime import datetime

  from hpcperfstats.dbload.lib.date_utils import parse_start_end_dates
  from hpcperfstats.dbload.sync_acct import _sync_acct_cli_args

  dead_letter, argv = _sync_acct_cli_args(
    [
      "sync_acct.py",
      "--dead-letter-unimportable",
      "2023-09-18",
      "2023-09-19",
    ]
  )
  assert dead_letter is True
  start, end = parse_start_end_dates(argv, None, None)
  assert start == datetime(2023, 9, 18)
  assert end == datetime(2023, 9, 19)
  absent, plain = _sync_acct_cli_args(
    ["sync_acct.py", "2023-09-18", "2023-09-19"]
  )
  assert absent is False
  assert plain == ["sync_acct.py", "2023-09-18", "2023-09-19"]


@override_settings(DEBUG=True)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_debug_moves_then_reraises(mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import (
    AccountingFileLoadError,
    _load_acct_file_for_daemon,
  )

  path = tmp_path / "2024-06-01.txt"
  body = _header_and_row_without("JobID")
  path.write_text(body)
  with pytest.raises(AccountingFileLoadError):
    _load_acct_file_for_daemon(str(path), set(), dead_letter=True)
  dest = tmp_path / ".unimportable-joblist" / "2024-06-01.txt"
  assert not path.exists()
  assert dest.read_text() == body
  assert any(
    "Unable to load file:" in line for line in _dead_letter_logs(mock_log)
  )


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_summary_names_each_file_and_reason(mock_log):
  from hpcperfstats.dbload.sync_acct import _log_dead_letter_summary

  _log_dead_letter_summary(
    [
      (
        "/acct/2023-09-18.txt",
        "/acct/.unimportable-joblist/2023-09-18.txt",
        "missing required sacct columns: JobID",
      ),
      (
        "/acct/2023-09-19.txt",
        "/acct/.unimportable-joblist/2023-09-19.txt",
        "cannot read accounting file: Permission denied (errno 13)",
      ),
    ]
  )
  lines = _dead_letter_logs(mock_log)
  assert lines[0] == "Dead-lettered accounting files: 2"
  assert (
    "Dead-lettered /acct/2023-09-18.txt -> "
    "/acct/.unimportable-joblist/2023-09-18.txt: "
    "missing required sacct columns: JobID"
  ) in lines
  assert (
    "Dead-lettered /acct/2023-09-19.txt -> "
    "/acct/.unimportable-joblist/2023-09-19.txt: "
    "cannot read accounting file: Permission denied (errno 13)"
  ) in lines


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_summary_reports_none(mock_log):
  from hpcperfstats.dbload.sync_acct import _log_dead_letter_summary

  _log_dead_letter_summary([])
  assert _dead_letter_logs(mock_log) == ["Dead-lettered accounting files: 0"]


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_summary_records_a_move(mock_log, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  path.write_text(_header_and_row_without("JobID"))
  moved = []
  _load_acct_file_for_daemon(
    str(path), set(), dead_letter=True, dead_lettered=moved
  )
  dest = tmp_path / ".unimportable-joblist" / "2024-06-01.txt"
  assert moved == [
    (str(path), str(dest), "missing required sacct columns: JobID")
  ]


@override_settings(DEBUG=False)
@patch("hpcperfstats.dbload.sync_acct.time.time", return_value=1700000000)
@patch("hpcperfstats.dbload.sync_acct.log_print")
def test_dead_letter_summary_skips_a_failed_move(mock_log, _time, tmp_path):
  from hpcperfstats.dbload.sync_acct import _load_acct_file_for_daemon

  path = tmp_path / "2024-06-01.txt"
  path.write_text(_header_and_row_without("JobID"))
  box = tmp_path / ".unimportable-joblist"
  box.mkdir()
  (box / "2024-06-01.txt").write_text("KEEP")
  (box / "2024-06-01.txt.1700000000").write_text("ALSO")
  moved = []
  _load_acct_file_for_daemon(
    str(path), set(), dead_letter=True, dead_lettered=moved
  )
  assert moved == []


def test_dead_letter_summary_runs_at_end_of_main():
  from pathlib import Path

  text = Path("hpcperfstats/dbload/sync_acct.py").read_text()
  main = text.split('if __name__ == "__main__":', 1)[1]
  assert "dead_lettered: list[tuple[str, str, str]] = []" in main
  assert "_log_dead_letter_summary(dead_lettered)" in main
  assert "finally:" in main
