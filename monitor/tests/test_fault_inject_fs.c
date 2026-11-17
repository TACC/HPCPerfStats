/* Fault coverage for the OS / filesystem collectors (built with -DDEBUG in every build): host_proc,
 * host_net, host_tmpfs, beegfs_client, lustre_llite and amd_gpu. Each failure site is forced
 * through a debug_fault_inject point (scandir, net_rebuild_slow, statfs) or a fixture (missing or
 * unreadable mounts / stats / capacity files); test_site_coverage.c then requires every
 * registered site in the binary to have fired. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "amd_gpu.h"
#undef KEYS
#include "beegfs_client.h"
#undef KEYS
#include "collect.h"
#include "collect_tier.h"
#include "host_tmpfs.h"
#include "lustre_llite.h"
#undef KEYS
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel_debug.h"
#include "test_rm_tree.h"

extern struct stats_type proc_stats_type;
extern struct stats_type net_stats_type;
extern struct stats_type tmpfs_stats_type;
extern struct stats_type beegfs_stats_type;
extern struct stats_type llite_stats_type;
extern struct stats_type amd_gpu_stats_type;

struct stats_type *const stats_type_table[] = {&proc_stats_type,  &net_stats_type,
                                               &tmpfs_stats_type, &beegfs_stats_type,
                                               &llite_stats_type, &amd_gpu_stats_type};
const size_t stats_type_nr = sizeof(stats_type_table) / sizeof(stats_type_table[0]);
double sample_freq = 300.0;

static char g_dir[] = "/tmp/hps_fault_fsXXXXXX";

/* host_net row counters are collect.c's job (covered by test_fault_inject_matrix). */
int path_collect_key_value_dir(const char *dir_path, struct stats *stats)
{
  (void)dir_path;
  (void)stats;
  return 0;
}

static const char *tmp_path(const char *name)
{
  static char path[256];

  snprintf(path, sizeof(path), "%s/%s", g_dir, name);
  return path;
}

static const char *write_tmp(const char *name, const char *content)
{
  const char *path = tmp_path(name);
  FILE *f = fopen(path, "w");

  assert(f != NULL);
  fputs(content, f);
  fclose(f);
  return path;
}

static unsigned int state(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void collect(struct stats_type *type)
{
  stats_type_mark_not_updated(type);
  type->st_collect(type);
  stats_fault_inject_tick();
}

/* scandir failure marks the rows and starts the cooldown; the next cycle is BACKOFF. */
static void test_proc_scandir_then_cooldown(void)
{
  struct stats *row = get_current_stats(&proc_stats_type, "kept/1/0/0");

  setenv("HPCPERFSTATS_PROC_COOLDOWN_SEC", "60", 1);
  assert(stats_fault_inject_set("scandir") == 1);
  collect(&proc_stats_type);
  assert(state(row, "vm_rss") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_set(NULL) == 0);
  collect(&proc_stats_type);
  assert(state(row, "vm_rss") == STATS_SENTINEL_BACKOFF);
}

/* A slow interface-cache rebuild skips the next rebuild: that cycle is BACKOFF on every row. */
static void test_net_rebuild_backoff(void)
{
  struct stats *row = get_current_stats(&net_stats_type, "lo");

  assert(stats_fault_inject_set("net_rebuild_slow") == 1);
  for (int i = 0; i < 40; i++)
    collect(&net_stats_type);
  assert(state(row, "rx_bytes") == STATS_SENTINEL_BACKOFF);
  assert(stats_fault_inject_set(NULL) == 0);
}

static void test_tmpfs(void)
{
  struct stats *row;

  tmpfs_test_set_mounts_path(tmp_path("no_mounts"));
  collect(&tmpfs_stats_type); /* setmntent fails: errno code, path latched */
  collect(&tmpfs_stats_type); /* latched: READ_FAILED */
  tmpfs_test_set_mounts_path(write_tmp("tmpfs_mounts", "tmpfs /tmp tmpfs rw 0 0\n"));
  assert(stats_fault_inject_set("statfs:/tmp") == 1);
  collect(&tmpfs_stats_type);
  row = get_current_stats(&tmpfs_stats_type, "/tmp");
  assert(state(row, "bytes_used") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_set(NULL) == 0);
  collect(&tmpfs_stats_type);
  assert(state(row, "bytes_used") == STATS_SENTINEL_OK);
  tmpfs_test_set_mounts_path(NULL);
}

static void test_beegfs(void)
{
  struct stats *row;

  beegfs_test_set_mounts_path(tmp_path("no_beegfs_mounts"));
  collect(&beegfs_stats_type);
  collect(&beegfs_stats_type);
  beegfs_test_set_mounts_path(
      write_tmp("beegfs_mounts", "beegfs_nodev /nonexistent/hps_beegfs beegfs rw 0 0\n"));
  collect(&beegfs_stats_type); /* statvfs fails; the ctl refresh fails (no beegfs-ctl) */
  row = get_current_stats(&beegfs_stats_type, "/nonexistent/hps_beegfs");
  assert(state(row, "fs_bytes_total") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "vfs_read_ops") == STATS_SENTINEL_BACKEND_ERROR);
  collect(&beegfs_stats_type); /* refresh gate: the cached code is republished */
  assert(state(row, "vfs_read_ops") == STATS_SENTINEL_BACKEND_ERROR);
  beegfs_test_set_mounts_path(NULL);
}

static void test_llite(void)
{
  struct stats *row = get_current_stats(&llite_stats_type, "/scratch");
  char proc[256];
  char sys[256];

  snprintf(proc, sizeof(proc), "%s", tmp_path("proc"));
  snprintf(sys, sizeof(sys), "%s", tmp_path("sys"));
  assert(mkdir(proc, 0700) == 0 && mkdir(sys, 0700) == 0);
  stats_type_mark_not_updated(&llite_stats_type);
  lustre_llite_collect_obd(row, proc, sys, "fs-ffff0001");
  assert(state(row, "vfs_read_ops") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "fs_bytes_total") == STATS_SENTINEL_READ_FAILED);
}

static void test_amd_gpu(void)
{
  struct stats *row = get_current_stats(&amd_gpu_stats_type, "0");

  stats_type_mark_not_updated(&amd_gpu_stats_type);
  amd_gpu_publish_not_built(row);
  assert(state(row, "gpu_util") == STATS_SENTINEL_NOT_BUILT);
  assert(state(row, "gpu_count") == STATS_SENTINEL_OK);
}

int main(void)
{
  assert(mkdtemp(g_dir) != NULL);
  for (size_t i = 0; i < stats_type_nr; i++) {
    assert(stats_type_init(stats_type_table[i]) == 0);
    collect_tier_apply_to_type(stats_type_table[i]);
    stats_type_table[i]->st_enabled = 1;
  }

  test_proc_scandir_then_cooldown();
  test_net_rebuild_backoff();
  test_tmpfs();
  test_beegfs();
  test_llite();
  test_amd_gpu();

  for (size_t i = 0; i < stats_type_nr; i++)
    stats_type_destroy(stats_type_table[i]);
  assert(test_rm_tree(g_dir) == 0);
  printf("test_fault_inject_fs passed\n");
  return 0;
}
