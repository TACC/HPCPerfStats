/* Fault-injection matrix and failure-site coverage gate (built with -DDEBUG in every build).
 *
 * Forces each failure site of the linked collectors through the debug_fault_inject table (the
 * open / opendir points in fileio.h, path_read.c and path_open_fail_once.h) or a synthetic
 * input, checks the mapped code lands on the affected keys and that values resume after release
 * where the read path does not latch the failure, then FAILs on any registered stats_set_missing*
 * site in this binary that never fired. Linked collectors: collect.c helpers, host_cpu, host_ps,
 * host_vfs, lustre_osc, lustre_mdc. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "collect.h"
#include "collect_tier.h"
#include "lustre_mdc.h"
#undef KEYS
#include "lustre_osc.h"
#undef KEYS
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel_debug.h"
#include "test_rm_tree.h"

extern struct stats_type cpu_stats_type;
extern struct stats_type ps_stats_type;
extern struct stats_type vfs_stats_type;
extern struct stats_type osc_stats_type;
extern struct stats_type mdc_stats_type;
void cpu_stats_invalidate_file_caches(void);

static struct stats_type g_t;
struct stats_type *const stats_type_table[] = {
    &g_t, &cpu_stats_type, &ps_stats_type, &vfs_stats_type, &osc_stats_type, &mdc_stats_type};
const size_t stats_type_nr = sizeof(stats_type_table) / sizeof(stats_type_table[0]);

static char g_dir[] = "/tmp/hps_fault_matrixXXXXXX";

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

static struct stats *fresh(struct stats_type *type, const char *dev)
{
  struct stats *s = get_current_stats(type, dev);

  assert(s != NULL);
  stats_type_mark_not_updated(type);
  return s;
}

static void collect(struct stats_type *type)
{
  stats_type_mark_not_updated(type);
  type->st_collect(type);
  stats_fault_inject_tick();
}

/* Every collect.c helper failure site, through synthetic input or the open/opendir points. */
static void test_collect_helpers(void)
{
  struct stats *s;
  char sub[256];

  s = fresh(&g_t, "str");
  assert(str_collect_key_list("1 x", s, "a", "b", NULL) == 1);
  assert(state(s, "b") == STATS_SENTINEL_READ_FAILED);
  s = fresh(&g_t, "pre");
  assert(str_collect_prefix_key_list("4", s, "", "a", "b", NULL) == 1);
  assert(state(s, "b") == STATS_SENTINEL_READ_FAILED);

  s = fresh(&g_t, "list");
  assert(path_collect_key_list(write_tmp("short", "5\n"), s, "a", "b", NULL) == 1);
  assert(state(s, "b") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_set("open:list_eacces!13") == 1);
  s = fresh(&g_t, "list");
  assert(path_collect_key_list(write_tmp("list_eacces", "1 2\n"), s, "a", "b", NULL) == -1);
  assert(state(s, "a") == STATS_SENTINEL_PERMISSION_DENIED);

  assert(stats_fault_inject_set("open:kv_eio") == 1);
  s = fresh(&g_t, "kv");
  assert(path_collect_key_value(write_tmp("kv_eio", "a 1\n"), s) == -1);
  assert(state(s, "a") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_set(NULL) == 0);
  s = fresh(&g_t, "kv");
  assert(path_collect_key_value(write_tmp("kv_ok", "a 1\nb junk\n"), s) == 0);
  assert(state(s, "a") == STATS_SENTINEL_OK && state(s, "b") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "c") == STATS_SENTINEL_NOT_PRESENT);

  snprintf(sub, sizeof(sub), "%s", tmp_path("dir"));
  assert(mkdir(sub, 0700) == 0);
  write_tmp("dir/a", "3\n");
  write_tmp("dir/b", "junk\n");
  s = fresh(&g_t, "dir");
  assert(path_collect_key_value_dir(sub, s) == 0);
  assert(state(s, "a") == STATS_SENTINEL_OK && state(s, "b") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "c") == STATS_SENTINEL_NOT_PRESENT);
  assert(stats_fault_inject_set("opendir:/dir") == 1);
  s = fresh(&g_t, "dir");
  assert(path_collect_key_value_dir(sub, s) == -1);
  assert(state(s, "a") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_set(NULL) == 0);
}

/* host_vfs reads through the latching collect path: the code persists after release. */
static void test_vfs(void)
{
  struct stats *s;

  assert(stats_fault_inject_set("open:/proc/sys/fs/@1") == 1);
  collect(&vfs_stats_type);
  s = get_current_stats(&vfs_stats_type, NULL);
  assert(state(s, "dentry_use") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "file_use") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "inode_use") == STATS_SENTINEL_READ_FAILED);
  assert(stats_fault_inject_fired(0) == 3);
}

/* host_ps /proc/loadavg (pscanf, no latch): fails for one cycle, then values resume. */
static void test_ps_loadavg_resumes(void)
{
  struct stats *s;

  assert(stats_fault_inject_set("open:/proc/loadavg@1") == 1);
  collect(&ps_stats_type);
  s = get_current_stats(&ps_stats_type, NULL);
  assert(state(s, "load_1") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "nr_threads") == STATS_SENTINEL_READ_FAILED);
  collect(&ps_stats_type);
  assert(state(s, "load_1") == STATS_SENTINEL_OK);
}

/* /proc/stat: host_cpu marks its rows with the open errno, then (path latched) the skip code;
 * host_ps then cannot open /proc/stat either. */
static void test_proc_stat(void)
{
  struct stats *cpu0;
  struct stats *ps;

  collect(&cpu_stats_type);
  cpu0 = get_current_stats(&cpu_stats_type, "0");
  assert(state(cpu0, "user") == STATS_SENTINEL_OK);
  cpu_stats_invalidate_file_caches();
  assert(stats_fault_inject_set("open:/proc/stat!13") == 1);
  collect(&cpu_stats_type);
  assert(state(cpu0, "user") == STATS_SENTINEL_PERMISSION_DENIED);
  assert(stats_fault_inject_set(NULL) == 0);
  collect(&cpu_stats_type);
  assert(state(cpu0, "idle") == STATS_SENTINEL_READ_FAILED);

  collect(&ps_stats_type);
  ps = get_current_stats(&ps_stats_type, NULL);
  assert(state(ps, "ctxt") == STATS_SENTINEL_READ_FAILED);
  assert(state(ps, "load_1") == STATS_SENTINEL_OK);
}

/* Lustre OBD stats (file_fopen_read, no latch): EIO on the mount row, then values resume. */
static void test_lustre_obd(void)
{
  char obd[256];
  struct stats *osc;
  struct stats *mdc;

  snprintf(obd, sizeof(obd), "%s", tmp_path("fs-OST0000-osc-x"));
  assert(mkdir(obd, 0700) == 0);
  write_tmp("fs-OST0000-osc-x/stats", "ost_read 2 samples [reqs]\n");
  osc = fresh(&osc_stats_type, "/scratch");
  mdc = fresh(&mdc_stats_type, "/scratch");

  assert(stats_fault_inject_set("open:fs-OST0000-osc-x") == 1);
  lustre_osc_collect_obd(osc, g_dir, "fs-OST0000-osc-x");
  lustre_mdc_collect_obd(mdc, g_dir, "fs-OST0000-osc-x");
  assert(state(osc, "ost_read") == STATS_SENTINEL_READ_FAILED);
  assert(state(mdc, "mds_close") == STATS_SENTINEL_READ_FAILED);

  assert(stats_fault_inject_set(NULL) == 0);
  stats_type_mark_not_updated(&osc_stats_type);
  lustre_osc_collect_obd(osc, g_dir, "fs-OST0000-osc-x");
  assert(state(osc, "ost_read") == STATS_SENTINEL_OK);
  assert(osc->s_val[schema_ref(&osc_stats_type.st_schema, "ost_read")] == 2);
}

int main(void)
{
  size_t never;
  assert(mkdtemp(g_dir) != NULL);
  snprintf(g_t.st_name, sizeof(g_t.st_name), "%s", "host_tt");
  g_t.st_schema_def = (char *)"a,E b,E c,E";
  for (size_t i = 0; i < stats_type_nr; i++) {
    assert(stats_type_init(stats_type_table[i]) == 0);
    collect_tier_apply_to_type(stats_type_table[i]);
    stats_type_table[i]->st_enabled = 1;
  }

  test_collect_helpers();
  test_vfs();
  test_ps_loadavg_resumes();
  test_proc_stat();
  test_lustre_obd();

  never = stats_sentinel_sites_report(NULL);
  if (never != 0) {
    fprintf(stderr, "test_fault_inject_matrix: %zu failure site(s) never fired:\n", never);
    (void)stats_sentinel_sites_report(stderr);
  }
  assert(never == 0);

  for (size_t i = 0; i < stats_type_nr; i++)
    stats_type_destroy(stats_type_table[i]);
  assert(test_rm_tree(g_dir) == 0);
  printf("test_fault_inject_matrix passed\n");
  return 0;
}
