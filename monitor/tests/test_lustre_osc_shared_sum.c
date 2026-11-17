/* lustre_osc: several OSCs share one mount row, and the row must publish the sum of the current
 * OSC lifetime counters on every tick. The old stats_inc path re-added each lifetime onto the
 * persisting row, so tick 2 published twice the lifetime sum. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "lustre_osc.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"
#include "test_rm_tree.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
extern struct stats_type osc_stats_type;

static char g_dir[] = "/tmp/hps_osc_sumXXXXXX";
static const char *const g_obds[] = {"fs-OST0000-osc-ffff0001", "fs-OST0001-osc-ffff0001"};

static void write_obd_stats(const char *obd, const char *content)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", g_dir, obd);
  (void)mkdir(path, 0700);
  snprintf(path, sizeof(path), "%s/%s/stats", g_dir, obd);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(content, f);
  fclose(f);
}

static void remove_obd_stats(const char *obd)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/%s/stats", g_dir, obd);
  assert(unlink(path) == 0);
}

static void run_tick(struct stats *row)
{
  stats_type_mark_not_updated(&osc_stats_type);
  for (size_t i = 0; i < sizeof(g_obds) / sizeof(g_obds[0]); i++)
    lustre_osc_collect_obd(row, g_dir, g_obds[i]);
}

static unsigned long long val(struct stats *row, const char *key)
{
  int i = schema_ref(&osc_stats_type.st_schema, key);

  assert(i >= 0 && row->s_val_state[i] == STATS_SENTINEL_OK);
  return row->s_val[i];
}

static unsigned int state(struct stats *row, const char *key)
{
  int i = schema_ref(&osc_stats_type.st_schema, key);

  assert(i >= 0);
  return row->s_val_state[i];
}

static void test_shared_mount_row_publishes_current_sum(struct stats *row)
{
  write_obd_stats(g_obds[0], "snapshot_time 1.0 secs.usecs\n"
                             "req_waittime 10 samples [usec] 1 9 300\n"
                             "read_bytes 4 samples [bytes] 1 4096 16384\n"
                             "ost_read 4 samples [reqs]\n");
  write_obd_stats(g_obds[1], "snapshot_time 1.0 secs.usecs\n"
                             "req_waittime 6 samples [usec] 1 9 100\n"
                             "read_bytes 1 samples [bytes] 1 4096 4096\n"
                             "ost_read 1 samples [reqs]\n");

  run_tick(row);
  assert(val(row, "read_bytes") == 16384ULL + 4096ULL);
  assert(val(row, "ost_read") == 5ULL);
  assert(val(row, "reqs") == 16ULL);
  assert(val(row, "wait") == 400ULL);

  /* tick 2: the same lifetimes, so the same sum (the accumulate bug gave twice it). */
  run_tick(row);
  assert(val(row, "read_bytes") == 16384ULL + 4096ULL);
  assert(val(row, "ost_read") == 5ULL);
  assert(val(row, "reqs") == 16ULL);
  assert(val(row, "wait") == 400ULL);

  /* tick 3: one OSC advanced; the row follows the new current sum. */
  write_obd_stats(g_obds[0], "req_waittime 11 samples [usec] 1 9 310\n"
                             "read_bytes 5 samples [bytes] 1 4096 20480\n"
                             "ost_read 5 samples [reqs]\n");
  run_tick(row);
  assert(val(row, "read_bytes") == 20480ULL + 4096ULL);
  assert(val(row, "ost_read") == 6ULL);
  assert(val(row, "reqs") == 17ULL);
}

/* A mount with an unreadable OSC must not publish the partial sum, whichever OSC fails. */
static void test_unreadable_osc_marks_the_mount_row(struct stats *row)
{
  remove_obd_stats(g_obds[1]);
  run_tick(row);
  assert(state(row, "read_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "ost_write") == STATS_SENTINEL_NOT_PRESENT);

  write_obd_stats(g_obds[1], "ost_read 1 samples [reqs]\n");
  remove_obd_stats(g_obds[0]);
  run_tick(row);
  assert(state(row, "read_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "ost_read") == STATS_SENTINEL_NOT_PRESENT);
}

int main(void)
{
  struct stats *row;
  assert(mkdtemp(g_dir) != NULL);
  assert(stats_type_init(&osc_stats_type) == 0);
  row = get_current_stats(&osc_stats_type, "/scratch");
  assert(row != NULL);

  test_shared_mount_row_publishes_current_sum(row);
  test_unreadable_osc_marks_the_mount_row(row);

  stats_type_destroy(&osc_stats_type);
  assert(test_rm_tree(g_dir) == 0);
  printf("test_lustre_osc_shared_sum passed\n");
  return 0;
}
