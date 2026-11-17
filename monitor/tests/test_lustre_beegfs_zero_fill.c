/* Lustre mdc/osc and BeeGFS clientstats list only non-zero counters: a readable source publishes
 * 0 for the keys it omits (not NOT_UPDATED), an unreadable one its errno code, and a BeeGFS tick
 * the ctl refresh gate skips re-publishes the cached values and codes. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "beegfs_client.h"
#undef KEYS
#include "lustre_mdc.h"
#undef KEYS
#include "lustre_osc.h"
#undef KEYS
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
double sample_freq = 300.0;
extern struct stats_type mdc_stats_type;
extern struct stats_type osc_stats_type;
extern struct stats_type beegfs_stats_type;

static char g_dir[] = "/tmp/hps_zero_fillXXXXXX";

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

static unsigned int state(struct stats *row, const char *key)
{
  int i = schema_ref(&row->s_type->st_schema, key);

  assert(i >= 0);
  return row->s_val_state[i];
}

static unsigned long long val(struct stats *row, const char *key)
{
  int i = schema_ref(&row->s_type->st_schema, key);

  assert(i >= 0 && row->s_val_state[i] == STATS_SENTINEL_OK);
  return row->s_val[i];
}

static void test_mdc_zero_fills_omitted_ops(void)
{
  struct stats *row;

  assert(stats_type_init(&mdc_stats_type) == 0);
  row = get_current_stats(&mdc_stats_type, "/scratch");
  write_obd_stats("fs-MDT0000-mdc-ffff0001", "snapshot_time 1.0 secs.usecs\n"
                                             "req_waittime 7 samples [usec] 1 9 70\n"
                                             "close 3 samples [reqs]\n");
  stats_type_mark_not_updated(&mdc_stats_type);
  lustre_mdc_collect_obd(row, g_dir, "fs-MDT0000-mdc-ffff0001");
  assert(val(row, "mds_close") == 3ULL);
  assert(val(row, "reqs") == 7ULL);
  assert(val(row, "wait") == 70ULL);
  assert(val(row, "mds_getattr") == 0ULL);
  assert(val(row, "ldlm_cancel") == 0ULL);

  stats_type_mark_not_updated(&mdc_stats_type);
  lustre_mdc_collect_obd(row, g_dir, "fs-MDT0001-mdc-absent");
  assert(state(row, "mds_close") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "reqs") == STATS_SENTINEL_NOT_PRESENT);
  stats_type_destroy(&mdc_stats_type);
}

static void test_osc_zero_fills_omitted_ops(void)
{
  struct stats *row;

  assert(stats_type_init(&osc_stats_type) == 0);
  row = get_current_stats(&osc_stats_type, "/scratch");
  write_obd_stats("fs-OST0000-osc-ffff0001", "snapshot_time 1.0 secs.usecs\n"
                                             "ost_write 2 samples [reqs]\n");
  stats_type_mark_not_updated(&osc_stats_type);
  lustre_osc_collect_obd(row, g_dir, "fs-OST0000-osc-ffff0001");
  assert(val(row, "ost_write") == 2ULL);
  assert(val(row, "read_bytes") == 0ULL);
  assert(val(row, "wait") == 0ULL);
  stats_type_destroy(&osc_stats_type);
}

static void test_beegfs_zero_fill_and_gated_republish(void)
{
  struct beegfs_ctl_counters storage;
  struct beegfs_ctl_counters meta;
  struct stats *row;

  assert(stats_type_init(&beegfs_stats_type) == 0);
  row = get_current_stats(&beegfs_stats_type, "/beegfs");
  memset(&storage, 0, sizeof(storage));
  memset(&meta, 0, sizeof(meta));
  storage.vfs_read_bytes = 4096;
  storage.have_vfs_read_bytes = 1;
  storage.vfs_close_ops = 1;
  storage.have_vfs_close_ops = 1;
  meta.vfs_close_ops = 9;
  meta.have_vfs_close_ops = 1;

  /* Refresh tick, both lines selected: omitted columns are 0, meta wins close. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_ctl(row, &storage, &meta);
  assert(val(row, "vfs_read_bytes") == 4096ULL);
  assert(val(row, "vfs_close_ops") == 9ULL);
  assert(val(row, "vfs_mkdir_ops") == 0ULL);

  /* Gated tick: the cached values are published again. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_ctl(row);
  assert(val(row, "vfs_read_bytes") == 4096ULL);
  assert(val(row, "vfs_close_ops") == 9ULL);
  assert(val(row, "vfs_mkdir_ops") == 0ULL);

  /* Only the storage line: its columns publish, the others cannot be told from absent. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_ctl(row, &storage, NULL);
  assert(val(row, "vfs_read_bytes") == 4096ULL);
  assert(val(row, "vfs_close_ops") == 1ULL);
  assert(state(row, "vfs_mkdir_ops") == STATS_SENTINEL_BACKEND_ERROR);

  /* Failed refresh, then a gated tick: the failure code is re-published too. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_ctl(row, NULL, NULL);
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_BACKEND_ERROR);
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_ctl(row);
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state(row, "fs_bytes_total") == STATS_SENTINEL_NOT_UPDATED);

  /* A mount never refreshed has nothing cached: its keys stay NOT_UPDATED. */
  row = get_current_stats(&beegfs_stats_type, "/beegfs2");
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_ctl(row);
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_NOT_UPDATED);
  stats_type_destroy(&beegfs_stats_type);
}

int main(void)
{
  char cmd[600];

  assert(mkdtemp(g_dir) != NULL);
  test_mdc_zero_fills_omitted_ops();
  test_osc_zero_fills_omitted_ops();
  test_beegfs_zero_fill_and_gated_republish();
  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", g_dir);
  assert(system(cmd) == 0);
  printf("test_lustre_beegfs_zero_fill passed\n");
  return 0;
}
