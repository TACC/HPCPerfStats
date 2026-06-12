import pytest

from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
    IngestArchiveLookupBudgetExceededError,
    set_ingest_task_deadline_monotonic,
    reset_ingest_task_deadline_monotonic,
)
from hpcperfstats.dbload.sync_timedb_parsing import find_processing_start_index


def test_find_processing_start_index_raises_on_ingest_deadline():
  lines = []
  base = 1_000_000
  for i in range(2500):
    lines.append("%d 1 host\n" % (base + i))
  itimes_set = {base + i for i in range(2500)}
  token = set_ingest_task_deadline_monotonic(0.0)
  try:
    with pytest.raises(IngestArchiveLookupBudgetExceededError):
      find_processing_start_index(lines, itimes_set)
  finally:
    reset_ingest_task_deadline_monotonic(token)
