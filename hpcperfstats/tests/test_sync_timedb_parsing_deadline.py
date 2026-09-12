from hpcperfstats.dbload.lib.sync_timedb_parsing import find_processing_start_index
from hpcperfstats.dbload.lib import sync_timedb_archive_members_coord as coord


def test_find_processing_start_index_ignores_ingest_wall_deadline():
  lines = []
  base = 1_000_000
  for i in range(2500):
    lines.append("%d 1 host\n" % (base + i))
  itimes_set = {base + i for i in range(2500)}
  assert not hasattr(coord, "set_ingest_task_deadline_monotonic")
  assert find_processing_start_index(lines, itimes_set)[0] == -1
