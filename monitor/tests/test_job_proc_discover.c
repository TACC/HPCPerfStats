/* Phase 1: job_proc_discover fixtures (c122-018 cgroup shapes). */
#include <assert.h>
#include <errno.h>
#include <ftw.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "job_proc_discover.h"

static char g_root[256];
/* Every fixture tree lives under one parent so main() can remove it all. */
static char g_parent[256];

static void write_file(const char *path, const char *data)
{
  FILE *f = fopen(path, "we");

  assert(f != NULL);
  fputs(data, f);
  fclose(f);
}

static void mk_pid(int pid, uid_t uid, const char *cgroup, const char *environ_blob,
                   size_t environ_len)
{
  char dir[512];
  char path[512];
  char status[256];

  snprintf(dir, sizeof(dir), "%s/%d", g_root, pid);
  assert(mkdir(dir, 0755) == 0);
  snprintf(path, sizeof(path), "%s/cgroup", dir);
  write_file(path, cgroup);
  snprintf(path, sizeof(path), "%s/environ", dir);
  {
    FILE *f = fopen(path, "we");

    assert(f != NULL);
    if (environ_blob != NULL && environ_len > 0)
      fwrite(environ_blob, 1, environ_len, f);
    fclose(f);
  }
  snprintf(status, sizeof(status), "Name:\ttask\nUid:\t%u\t%u\t%u\t%u\n", uid, uid, uid, uid);
  snprintf(path, sizeof(path), "%s/status", dir);
  write_file(path, status);
  /* Filter uses st_uid of the process directory (stat) and skips root-owned dirs. As root
   * every fixture dir would be root-owned, so give each one its intended owner (make check
   * as root on Horizon c122-018 dropped every job pid: r.n == 0). */
  if (geteuid() == 0)
    assert(chown(dir, uid, uid) == 0);
}

static void test_jid_acceptable(void)
{
  assert(job_proc_jid_acceptable("12345"));
  assert(!job_proc_jid_acceptable("0"));
  assert(!job_proc_jid_acceptable("-"));
  assert(!job_proc_jid_acceptable(""));
  assert(!job_proc_jid_acceptable("12 34"));
  assert(!job_proc_jid_acceptable("123456789012345678901234567890123")); /* 33 chars */
}

static void test_scope_token_grouping(void)
{
  struct job_proc_discover_result r;
  /* NUL-separated environ blobs (cannot use C string literals past first \\0). */
  static const char env1[] = {
      'S',  'L', 'U', 'R', 'M', '_', 'J', 'O', 'B', '_', 'I', 'D', '=', '6', '2', '8', '8', '8',
      '\0', 'S', 'L', 'U', 'R', 'M', '_', 'J', 'O', 'B', '_', 'G', 'P', 'U', 'S', '=', '0', '\0',
      'S',  'L', 'U', 'R', 'M', '_', 'S', 'T', 'E', 'P', '_', 'I', 'D', '=', '-', '5', '\0'};
  static const char env2[] = {
      'S',  'L', 'U', 'R', 'M', '_', 'J', 'O', 'B', '_', 'I', 'D', '=', '6', '2', '8', '9', '2',
      '\0', 'S', 'L', 'U', 'R', 'M', '_', 'J', 'O', 'B', '_', 'G', 'P', 'U', 'S', '=', '2', '\0',
      'S',  'L', 'U', 'R', 'M', '_', 'S', 'T', 'E', 'P', '_', 'I', 'D', '=', '-', '5', '\0'};
  static const char env3[] = {'S', 'L', 'U', 'R', 'M', '_', 'J', 'O', 'B', '_',
                              'I', 'D', '=', '9', '9', '9', '9', '9', '\0'};
  /* env-less sibling inherits token jid */
  const char *cg1 = "0::/system.slice/slurmstepd.scope/s4TDM5AXNE9900/step_batch/user/task_0\n";
  const char *cg1b =
      "0::/system.slice/slurmstepd.scope/s4TDM5AXNE9900/step_extern/user/task_special\n";
  const char *cg2 = "0::/system.slice/slurmstepd.scope/s4TDM5AXRQG500/step_batch/user/task_0\n";
  const char *cg_job_text =
      "0::/system.slice/slurmstepd.scope/other/job_33333/step_batch/user/task_0\n";

  assert(mkdir(g_root, 0755) == 0 || errno == EEXIST);
  setenv("HPCPERFSTATS_PROC_ROOT", g_root, 1);

  mk_pid(1001, 1000, cg1, env1, sizeof(env1));
  mk_pid(1002, 1000, cg1b, NULL, 0); /* inherits 62888 */
  mk_pid(2001, 1000, cg2, env2, sizeof(env2));
  mk_pid(3001, 1000, cg_job_text, env3, sizeof(env3));

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 3);
  assert(strcmp(r.entries[0].jid, "62888") == 0);
  assert(strcmp(r.entries[1].jid, "62892") == 0);
  assert(strcmp(r.entries[2].jid, "99999") == 0);
  assert(r.entries[0].npids >= 2);
  assert(strcmp(r.entries[0].job_gpus, "0") == 0);
  assert(strstr(r.entries[0].cgroup_relpath, "/slurmstepd.scope/s4TDM5AXNE9900/") != NULL);
  assert(r.entries[0].cgroup_relpath[0] == '/');
  job_proc_discover_result_clear(&r);
}

static void test_rejects_root_and_bad_jid(void)
{
  struct job_proc_discover_result r;
  const char *cg = "0::/system.slice/slurmstepd.scope/tokA/step_batch/user/task_0\n";

  setenv("HPCPERFSTATS_PROC_ROOT", g_root, 1);
  /* Fresh root */
  snprintf(g_root, sizeof(g_root), "%s/proc_disc2_XXXXXX", g_parent);
  assert(mkdtemp(g_root) != NULL);
  setenv("HPCPERFSTATS_PROC_ROOT", g_root, 1);

  {
    static const char bad[] = {'S', 'L', 'U', 'R', 'M', '_', 'J', 'O',
                               'B', '_', 'I', 'D', '=', '0', '\0'};
    static const char rootjid[] = {'S', 'L', 'U', 'R', 'M', '_', 'J', 'O',
                                   'B', '_', 'I', 'D', '=', '1', '\0'};

    mk_pid(4002, 1000, cg, bad, sizeof(bad)); /* bad jid rejected */
    if (geteuid() == 0)
      mk_pid(4001, 0, cg, rootjid, sizeof(rootjid)); /* uid 0 skipped by filter */
  }

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 0);
  job_proc_discover_result_clear(&r);
}

/* Fresh fake /proc and cgroup roots (HPCPERFSTATS_PROC_ROOT / HPCPERFSTATS_CGROUP_ROOT). */
static char g_cg_root[256];

static void fresh_roots(void)
{
  snprintf(g_root, sizeof(g_root), "%s/proc_title_XXXXXX", g_parent);
  assert(mkdtemp(g_root) != NULL);
  snprintf(g_cg_root, sizeof(g_cg_root), "%s/cg_title_XXXXXX", g_parent);
  assert(mkdtemp(g_cg_root) != NULL);
  setenv("HPCPERFSTATS_PROC_ROOT", g_root, 1);
  setenv("HPCPERFSTATS_CGROUP_ROOT", g_cg_root, 1);
}

static void mkdir_p(const char *path)
{
  char buf[512];
  size_t i;

  snprintf(buf, sizeof(buf), "%s", path);
  for (i = 1; buf[i] != '\0'; i++) {
    if (buf[i] == '/') {
      buf[i] = '\0';
      assert(mkdir(buf, 0755) == 0 || errno == EEXIST);
      buf[i] = '/';
    }
  }
  assert(mkdir(buf, 0755) == 0 || errno == EEXIST);
}

/* Scope token dir with step_extern/slurm/cgroup.procs listing procs (NULL = no file). */
static void mk_scope_token(const char *token, const char *procs)
{
  char dir[512];
  char path[600];

  snprintf(dir, sizeof(dir), "%s/system.slice/slurmstepd.scope/%s/step_extern/slurm", g_cg_root,
           token);
  mkdir_p(dir);
  if (procs != NULL) {
    snprintf(path, sizeof(path), "%s/cgroup.procs", dir);
    write_file(path, procs);
  }
}

/* Root slurmstepd: /proc/<pid>/cmdline only (production filter skips root pid dirs). */
static void mk_stepd(int pid, const char *title, size_t title_len)
{
  char dir[512];
  char path[600];
  FILE *f;

  snprintf(dir, sizeof(dir), "%s/%d", g_root, pid);
  assert(mkdir(dir, 0755) == 0);
  snprintf(path, sizeof(path), "%s/cmdline", dir);
  f = fopen(path, "we");
  assert(f != NULL);
  fwrite(title, 1, title_len, f);
  fclose(f);
}

static void test_stepd_title_parse(void)
{
  char jid[JOB_PROC_JID_MAX + 1];
  static const char ext[] = "slurmstepd: [79249.extern]";
  static const char ns[] = "slurmstepd: [79249:namespace]";
  static const char batch[] = "slurmstepd: [79249.batch]\0\0";
  static const char long_jid[] = "slurmstepd: [123456789012345678901234567890123.extern]";

  assert(job_proc_jid_from_stepd_title(ext, sizeof(ext) - 1, jid, sizeof(jid)) == 0);
  assert(strcmp(jid, "79249") == 0);
  assert(job_proc_jid_from_stepd_title(ns, sizeof(ns) - 1, jid, sizeof(jid)) == 0);
  assert(strcmp(jid, "79249") == 0);
  assert(job_proc_jid_from_stepd_title(batch, sizeof(batch) - 1, jid, sizeof(jid)) == 0);
  assert(strcmp(jid, "79249") == 0);

  assert(job_proc_jid_from_stepd_title("bash", 4, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: [abc.extern]", 24, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: []", 14, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: [", 13, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: [123", 16, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: [0.extern]", 22, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title("slurmstepd: [12+0.0]", 20, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title(long_jid, sizeof(long_jid) - 1, jid, sizeof(jid)) != 0);
  /* Prefix must sit at the start of the title. */
  assert(job_proc_jid_from_stepd_title("x slurmstepd: [5.extern]", 24, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title(NULL, 0, jid, sizeof(jid)) != 0);
  assert(job_proc_jid_from_stepd_title(ext, sizeof(ext) - 1, NULL, 0) != 0);
  assert(job_proc_jid_from_stepd_title(ext, sizeof(ext) - 1, jid, 3) != 0); /* too small */
}

static void test_names_root_only_scope_from_stepd_title(void)
{
  struct job_proc_discover_result r;
  static const char ext[] = "slurmstepd: [79249.extern]";
  static const char ns[] = "slurmstepd: [79250:namespace]";

  fresh_roots();
  mk_scope_token("s4TDM6EWYAH400", "5001\n");
  mk_stepd(5001, ext, sizeof(ext)); /* trailing NUL like a real cmdline */
  mk_scope_token("s4TDM6EWYAH500", "5101\n5102\n");
  mk_stepd(5101, ns, sizeof(ns) - 1);

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 2);
  assert(strcmp(r.entries[0].jid, "79249") == 0);
  assert(r.entries[0].named_from_title == 1);
  assert(r.entries[0].npids == 1 && r.entries[0].pids[0] == 5001);
  assert(strcmp(r.entries[0].cgroup_relpath,
                "/system.slice/slurmstepd.scope/s4TDM6EWYAH400/step_extern/slurm") == 0);
  assert(r.entries[0].job_gpus[0] == '\0');
  assert(r.entries[0].stepd_pid == 5001);
  assert(strcmp(r.entries[1].jid, "79250") == 0);
  assert(r.entries[1].named_from_title == 1);
  assert(r.entries[1].stepd_pid == 5101); /* first pid of cgroup.procs */
  job_proc_discover_result_clear(&r);
}

static void test_env_name_wins_over_stepd_title(void)
{
  struct job_proc_discover_result r;
  static const char env[] = {'S', 'L', 'U', 'R', 'M', '_',  'J', 'O', 'B', '_', 'I',
                             'D', '=', '1', '1', '1', '\0', 'S', 'L', 'U', 'R', 'M',
                             '_', 'S', 'T', 'E', 'P', '_',  'I', 'D', '=', '0', '\0'};
  static const char ext[] = "slurmstepd: [222.extern]";
  const char *cg = "0::/system.slice/slurmstepd.scope/tokEnv/step_0/user/task_0\n";

  fresh_roots();
  mk_pid(6001, 1000, cg, env, sizeof(env));
  mk_scope_token("tokEnv", "6000\n");
  mk_stepd(6000, ext, sizeof(ext) - 1);

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 1);
  assert(strcmp(r.entries[0].jid, "111") == 0);
  assert(r.entries[0].named_from_title == 0);
  /* Env-named bucket keeps the non-root leaf path; stepd pid is not added to pids. */
  assert(strstr(r.entries[0].cgroup_relpath, "/step_0/user/task_0") != NULL);
  assert(r.entries[0].npids == 1 && r.entries[0].pids[0] == 6001);
  /* M1: the extern stepd pid is still recorded so CPU coverage reads the job cpuset. */
  assert(r.entries[0].stepd_pid == 6000);
  job_proc_discover_result_clear(&r);
}

static void test_bad_title_stays_unnamed(void)
{
  struct job_proc_discover_result r;
  static const char bash_title[] = "-bash";
  static const char letters[] = "slurmstepd: [abc.extern]";

  fresh_roots();
  mk_scope_token("tokBad1", "7001\n");
  mk_stepd(7001, bash_title, sizeof(bash_title) - 1);
  mk_scope_token("tokBad2", "7002\n");
  mk_stepd(7002, letters, sizeof(letters) - 1);
  mk_scope_token("tokEmpty", "7003\n");
  mk_stepd(7003, "", 0);
  mk_scope_token("system", "7004\n"); /* slurmstepd.scope/system is never a job */
  mk_stepd(7004, "slurmstepd: [9.extern]", 22);

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 0);
  job_proc_discover_result_clear(&r);
}

static void test_vanished_root_pid_fails_open(void)
{
  struct job_proc_discover_result r;

  fresh_roots();
  mk_scope_token("tokGone", "8001\n"); /* no /proc/8001 */
  mk_scope_token("tokNoProcs", NULL);  /* no cgroup.procs */
  mk_scope_token("tokJunk", "notapid\n");

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 0);
  job_proc_discover_result_clear(&r);

  /* No slurmstepd.scope directory at all: unchanged empty result. */
  fresh_roots();
  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 0);
  job_proc_discover_result_clear(&r);
}

static void test_require_step_skips_env_only(void)
{
  struct job_proc_discover_result r;
  static const char env_nostep[] = {'S', 'L', 'U', 'R', 'M', '_', 'J', 'O', 'B',
                                    '_', 'I', 'D', '=', '3', '3', '3', '\0'};
  static const char ext[] = "slurmstepd: [444.extern]";
  const char *cg = "0::/system.slice/slurmstepd.scope/tokNoStep/step_extern/user/task_0\n";

  fresh_roots();
  mk_pid(9001, 1000, cg, env_nostep, sizeof(env_nostep));
  mk_scope_token("tokNoStep", NULL);
  mk_scope_token("tokTitle", "9100\n");
  mk_stepd(9100, ext, sizeof(ext) - 1);

  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 1, 4096) == 0);
  assert(r.n == 1);
  assert(strcmp(r.entries[0].jid, "444") == 0);
  assert(r.entries[0].named_from_title == 1);
  assert(r.entries[0].stepd_pid == 9100);
  job_proc_discover_result_clear(&r);

  /* Without the step filter both groups are kept. */
  memset(&r, 0, sizeof(r));
  assert(job_proc_discover(&r, 0, 4096) == 0);
  assert(r.n == 2);
  assert(strcmp(r.entries[0].jid, "333") == 0 && r.entries[0].named_from_title == 0);
  assert(r.entries[0].stepd_pid == 0); /* tokNoStep has no cgroup.procs: unknown */
  job_proc_discover_result_clear(&r);
}

static int rm_entry(const char *path, const struct stat *sb, int flag, struct FTW *ftw)
{
  (void)sb;
  (void)flag;
  (void)ftw;
  return remove(path);
}

int main(void)
{
  const char *tmpdir = getenv("TMPDIR");

  snprintf(g_parent, sizeof(g_parent), "%s/job_proc_disc_XXXXXX",
           tmpdir && tmpdir[0] ? tmpdir : "/tmp");
  assert(mkdtemp(g_parent) != NULL);
  snprintf(g_root, sizeof(g_root), "%s/proc_disc_XXXXXX", g_parent);
  assert(mkdtemp(g_root) != NULL);
  /* Point the cgroup root at an empty dir so the host's real cgroupfs is never read. */
  snprintf(g_cg_root, sizeof(g_cg_root), "%s/cg_disc_XXXXXX", g_parent);
  assert(mkdtemp(g_cg_root) != NULL);
  setenv("HPCPERFSTATS_CGROUP_ROOT", g_cg_root, 1);

  test_jid_acceptable();
  test_scope_token_grouping();
  test_rejects_root_and_bad_jid();
  test_stepd_title_parse();
  test_names_root_only_scope_from_stepd_title();
  test_env_name_wins_over_stepd_title();
  test_bad_title_stays_unnamed();
  test_vanished_root_pid_fails_open();
  test_require_step_skips_env_only();
  assert(nftw(g_parent, rm_entry, 16, FTW_DEPTH | FTW_PHYS) == 0);
  printf("test_job_proc_discover: ok\n");
  return 0;
}
