/* job_cgroup_mem: token-dir only, KB mapping, no leaf/aggregate fallback. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "job_cgroup_mem.h"

static char g_root[256];

static void write_file(const char *path, const char *data)
{
  FILE *f = fopen(path, "we");

  assert(f != NULL);
  fputs(data, f);
  fclose(f);
}

static void mkdirs_p(const char *path)
{
  char tmp[512];
  size_t i;
  size_t n;

  snprintf(tmp, sizeof(tmp), "%s", path);
  n = strlen(tmp);
  for (i = 1; i < n; i++) {
    if (tmp[i] == '/') {
      tmp[i] = '\0';
      if (mkdir(tmp, 0755) != 0 && errno != EEXIST)
        assert(0);
      tmp[i] = '/';
    }
  }
  if (mkdir(tmp, 0755) != 0 && errno != EEXIST)
    assert(0);
}

static void write_mem_tree_under(const char *root, const char *rel_dir, const char *current,
                                 const char *peak, const char *stat)
{
  char dir[512];
  char path[512];

  assert(snprintf(dir, sizeof(dir), "%s%s", root, rel_dir) < (int)sizeof(dir));
  mkdirs_p(dir);
  if (current != NULL) {
    assert(snprintf(path, sizeof(path), "%s/memory.current", dir) < (int)sizeof(path));
    write_file(path, current);
  }
  if (peak != NULL) {
    assert(snprintf(path, sizeof(path), "%s/memory.peak", dir) < (int)sizeof(path));
    write_file(path, peak);
  }
  if (stat != NULL) {
    assert(snprintf(path, sizeof(path), "%s/memory.stat", dir) < (int)sizeof(path));
    write_file(path, stat);
  }
}

static void write_mem_tree(const char *rel_dir, const char *current, const char *peak,
                           const char *stat)
{
  write_mem_tree_under(g_root, rel_dir, current, peak, stat);
}

static void test_token_dir_only(void)
{
  struct job_cgroup_mem_kb kb;
  const char *leaf = "/system.slice/slurmstepd.scope/TOKENABC/step_batch/user/task_0";
  const char *stat_tok = "anon 2048\nfile 4096\nfile_mapped 512\nfile_dirty 256\n"
                         "file_writeback 128\npagetables 64\n";
  const char *stat_leaf = "anon 999999\nfile 999999\n";

  assert(mkdir(g_root, 0755) == 0 || errno == EEXIST);
  setenv("HPCPERFSTATS_CGROUP_ROOT", g_root, 1);

  /* Distinct values at leaf vs token vs bare scope. */
  write_mem_tree("/system.slice/slurmstepd.scope/TOKENABC/step_batch/user/task_0", "1024\n",
                 "2048\n", stat_leaf);
  write_mem_tree("/system.slice/slurmstepd.scope/TOKENABC", "232521728\n", "405405696\n", stat_tok);
  write_mem_tree("/system.slice/slurmstepd.scope", "2660040704\n", "9999999999\n",
                 "anon 1\nfile 1\n");

  assert(job_cgroup_mem_read(leaf, &kb) == 0);
  assert(kb.mem_used == 232521728ULL / 1024ULL);
  assert(kb.mem_peak == 405405696ULL / 1024ULL);
  assert(kb.anon_pages == 2048ULL / 1024ULL);
  assert(kb.file_pages == 4096ULL / 1024ULL);
  assert(kb.mapped == 512ULL / 1024ULL);
  assert(kb.dirty == 256ULL / 1024ULL);
  assert(kb.writeback == 128ULL / 1024ULL);
  assert(kb.page_tables == 64ULL / 1024ULL);
}

static void test_missing_token_failopen(void)
{
  struct job_cgroup_mem_kb kb;
  char root2[256];

  snprintf(root2, sizeof(root2), "%s/cg2_XXXXXX", getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp");
  assert(mkdtemp(root2) != NULL);
  setenv("HPCPERFSTATS_CGROUP_ROOT", root2, 1);
  /* Leaf only, no token-dir memory.current */
  write_mem_tree_under(root2, "/system.slice/slurmstepd.scope/TOK/step_batch/user/task_0", "100\n",
                       NULL, NULL);
  assert(job_cgroup_mem_read("/system.slice/slurmstepd.scope/TOK/step_batch/user/task_0", &kb) ==
         -1);
}

static void test_bad_root(void)
{
  struct job_cgroup_mem_kb kb;

  setenv("HPCPERFSTATS_CGROUP_ROOT", "/tmp/../etc", 1);
  /* Falls back to /sys/fs/cgroup; empty relpath fails */
  assert(job_cgroup_mem_read("", &kb) == -1);
  assert(job_cgroup_mem_read(NULL, &kb) == -1);
}

static void test_emit_dev_filter_goldens(void)
{
  char dev[40];

  assert(job_cgroup_mem_format_dev("62909", dev, sizeof(dev)) == 0);
  assert(strcmp(dev, "j:62909") == 0);
  assert(strcmp(dev, "62909") != 0); /* never bare jid */

  /* Sole / marked jid payload keeps matching j:<jid>. */
  assert(job_cgroup_mem_keep_dev("62909", "j:62909") == 1);
  /* Multi-jid: other job's device omitted. */
  assert(job_cgroup_mem_keep_dev("62909", "j:111") == 0);
  /* "-" / idle (NULL jid) omits all host_mem_job. */
  assert(job_cgroup_mem_keep_dev(NULL, "j:62909") == 0);
  assert(job_cgroup_mem_keep_dev("", "j:62909") == 0);
  /* Bare jid never matches. */
  assert(job_cgroup_mem_keep_dev("62909", "62909") == 0);
}

int main(void)
{
  snprintf(g_root, sizeof(g_root), "%s/cg_mem_XXXXXX",
           getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp");
  assert(mkdtemp(g_root) != NULL);
  test_token_dir_only();
  test_missing_token_failopen();
  test_bad_root();
  test_emit_dev_filter_goldens();
  printf("test_job_cgroup_mem: ok\n");
  return 0;
}
