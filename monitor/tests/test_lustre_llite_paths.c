/* lustre_llite opcode stats location: procfs when present, else debugfs (Lustre 2.15+); when
 * neither is readable, a sysfs (capacity) instance gives its opcode keys the errno code instead
 * of leaving them NOT_UPDATED. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

#include "lustre_llite.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"
#include "test_rm_tree.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
double sample_freq = 300.0;
extern struct stats_type llite_stats_type;

static char g_dir[] = "/tmp/hps_llite_pathsXXXXXX";

/* Every instance maps to one row; the lov-based mapping needs a live client. */
char *lustre_obd_to_mnt(const char *name)
{
  static char mnt[] = "fs-clilov";

  return name != NULL ? mnt : NULL;
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

static void put(const char *rel, const char *content)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", g_dir, rel);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(content, f);
  fclose(f);
}

static void mk(const char *rel)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/%s", g_dir, rel);
  assert(mkdir(path, 0700) == 0);
}

/* One sysfs capacity instance under <root>/fs-ffff0001. */
static void make_sysfs(const char *root)
{
  char rel[256];

  mk(root);
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001", root);
  mk(rel);
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/kbytestotal", root);
  put(rel, "100\n");
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/kbytesfree", root);
  put(rel, "40\n");
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/kbytesavail", root);
  put(rel, "30\n");
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/filestotal", root);
  put(rel, "9\n");
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/filesfree", root);
  put(rel, "4\n");
}

/* One opcode stats instance under <root>/fs-ffff0001/stats with `opens` open calls. */
static void make_stats(const char *root, int opens)
{
  char rel[256];
  char text[256];

  mk(root);
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001", root);
  mk(rel);
  snprintf(rel, sizeof(rel), "%s/fs-ffff0001/stats", root);
  snprintf(text, sizeof(text),
           "snapshot_time 1.0 secs.nsecs\n"
           "open %d samples [usec] 1 9 20 100\n"
           "read_bytes 3 samples [bytes] 1 4096 8192\n",
           opens);
  put(rel, text);
}

static struct stats *collect(const char *proc, const char *debugfs, const char *sysfs)
{
  static char p[3][512];

  snprintf(p[0], sizeof(p[0]), "%s/%s", g_dir, proc);
  snprintf(p[1], sizeof(p[1]), "%s/%s", g_dir, debugfs);
  snprintf(p[2], sizeof(p[2]), "%s/%s", g_dir, sysfs);
  lustre_llite_test_set_roots(p[0], p[1], p[2]);
  assert(stats_type_init(&llite_stats_type) == 0);
  stats_type_mark_not_updated(&llite_stats_type);
  llite_stats_type.st_collect(&llite_stats_type);
  return get_current_stats(&llite_stats_type, "fs-clilov");
}

/* Lustre 2.15: no procfs llite dir, stats in debugfs. */
static void test_debugfs_when_no_procfs(void)
{
  struct stats *row;

  make_sysfs("a_sys");
  make_stats("a_dbg", 5);
  row = collect("a_proc_absent", "a_dbg", "a_sys");
  assert(val(row, "vfs_open_ops") == 5ULL);
  assert(val(row, "vfs_read_bytes") == 8192ULL);
  assert(val(row, "vfs_mkdir_ops") == 0ULL);
  assert(val(row, "fs_bytes_total") == 100ULL * 1024ULL);
  stats_type_destroy(&llite_stats_type);
}

/* Older clients: procfs wins even when debugfs also has a stats file. */
static void test_procfs_first(void)
{
  struct stats *row;

  make_sysfs("b_sys");
  make_stats("b_proc", 7);
  make_stats("b_dbg", 99);
  row = collect("b_proc", "b_dbg", "b_sys");
  assert(val(row, "vfs_open_ops") == 7ULL);
  stats_type_destroy(&llite_stats_type);
}

/* Neither stats location: opcode keys get NOT_PRESENT, capacity still reads. */
static void test_no_stats_location_sets_code(void)
{
  struct stats *row;

  make_sysfs("c_sys");
  row = collect("c_proc_absent", "c_dbg_absent", "c_sys");
  assert(state(row, "vfs_open_ops") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "vfs_rename_ops") == STATS_SENTINEL_NOT_PRESENT);
  assert(val(row, "fs_files_free") == 4ULL);
  stats_type_destroy(&llite_stats_type);
}

int main(void)
{
  assert(mkdtemp(g_dir) != NULL);
  test_debugfs_when_no_procfs();
  test_procfs_first();
  test_no_stats_location_sets_code();
  lustre_llite_test_set_roots(NULL, NULL, NULL);
  assert(test_rm_tree(g_dir) == 0);
  printf("test_lustre_llite_paths passed\n");
  return 0;
}
