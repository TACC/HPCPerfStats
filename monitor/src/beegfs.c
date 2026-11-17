/* beegfs_client - BeeGFS mount detection, statvfs capacity, bounded beegfs-ctl I/O. */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <mntent.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "beegfs_client.h"
#include "beegfs_ctl_parse.h"
#include "collect_tier.h"
#include "path_open_fail_once.h"
#include "stats.h"
#include "trace.h"

#ifdef RABBITMQ
#include "monitor_daemon.h"
#endif

#define BEEGFS_CTL_TIMEOUT_MS 5000
#define BEEGFS_CTL_OUT_MAX (256 * 1024)
#define BEEGFS_CTL_MIN_INTERVAL_S 30
#define BEEGFS_MAX_IDENTS 64
#define BEEGFS_MAX_MOUNTS 32

static time_t g_beegfs_last_ctl_refresh;
static int g_beegfs_ctl_fail_latched;
static int g_beegfs_rwunit_b = -1; /* -1 unknown, 0 MiB, 1 bytes */
static const char *g_beegfs_mounts = "/proc/mounts";

static const char *const beegfs_fs_keys[] = {"fs_bytes_total", "fs_bytes_free", "fs_bytes_avail",
                                             "fs_files_total", "fs_files_free"};

/* beegfs-ctl derived keys, in struct beegfs_ctl_counters field order. */
/* clang-format off */
#define BEEGFS_CTL_FIELDS                                                                          \
  X(vfs_read_bytes) X(vfs_write_bytes) X(vfs_read_ops) X(vfs_write_ops) X(vfs_open_ops)            \
  X(vfs_close_ops) X(vfs_getattr_ops) X(vfs_setattr_ops) X(vfs_truncate_ops) X(vfs_readdir_ops)    \
  X(vfs_create_ops) X(vfs_mkdir_ops) X(vfs_rmdir_ops) X(vfs_rename_ops) X(vfs_unlink_ops)          \
  X(vfs_link_ops) X(vfs_statfs_ops)
/* clang-format on */
#define X(f) #f,
static const char *const beegfs_ctl_keys[] = {BEEGFS_CTL_FIELDS};
#undef X
#define BEEGFS_CTL_NKEYS (sizeof(beegfs_ctl_keys) / sizeof(beegfs_ctl_keys[0]))

/* What the last ctl refresh published for one mount, re-published on ticks the refresh gate
 * skips. A mount beyond BEEGFS_MAX_MOUNTS is not cached and emits NOT_UPDATED on those ticks. */
struct beegfs_ctl_cache {
  char mnt[BEEGFS_CTL_ARGSTR_LEN];
  unsigned long long val[BEEGFS_CTL_NKEYS];
  unsigned char state[BEEGFS_CTL_NKEYS];
};
static struct beegfs_ctl_cache g_beegfs_ctl_cache[BEEGFS_MAX_MOUNTS];
static size_t g_beegfs_ctl_cache_n;

static void beegfs_collect_capacity(struct stats *stats, const char *mnt)
{
  struct statvfs sv;

  if (stats == NULL || mnt == NULL)
    return;
  if (statvfs(mnt, &sv) != 0) {
    enum stats_sentinel code = stats_sentinel_for_errno(errno);

    TRACE("beegfs: statvfs(%s) failed: %m\n", mnt);
    for (size_t i = 0; i < sizeof(beegfs_fs_keys) / sizeof(beegfs_fs_keys[0]); i++)
      stats_set_missing(stats, beegfs_fs_keys[i], code);
    return;
  }
  stats_set(stats, "fs_bytes_total",
            (unsigned long long)sv.f_frsize * (unsigned long long)sv.f_blocks);
  stats_set(stats, "fs_bytes_free",
            (unsigned long long)sv.f_frsize * (unsigned long long)sv.f_bfree);
  stats_set(stats, "fs_bytes_avail",
            (unsigned long long)sv.f_frsize * (unsigned long long)sv.f_bavail);
  stats_set(stats, "fs_files_total", (unsigned long long)sv.f_files);
  stats_set(stats, "fs_files_free", (unsigned long long)sv.f_ffree);
}

static int beegfs_ctl_field(const struct beegfs_ctl_counters *c, size_t i, unsigned long long *v)
{
  size_t n = 0;

#define X(f)                                                                                       \
  if (i == n++) {                                                                                  \
    *v = c->f;                                                                                     \
    return c->have_##f;                                                                            \
  }
  BEEGFS_CTL_FIELDS
#undef X
  return 0;
}

static struct beegfs_ctl_cache *beegfs_ctl_cache_find(const char *mnt)
{
  for (size_t i = 0; i < g_beegfs_ctl_cache_n; i++)
    if (strcmp(g_beegfs_ctl_cache[i].mnt, mnt) == 0)
      return &g_beegfs_ctl_cache[i];
  return NULL;
}

/* The cache slot of `mnt`, added on first use; NULL when the table is full. */
static struct beegfs_ctl_cache *beegfs_ctl_cache_for(const char *mnt)
{
  struct beegfs_ctl_cache *cache = beegfs_ctl_cache_find(mnt);

  if (cache != NULL || g_beegfs_ctl_cache_n == BEEGFS_MAX_MOUNTS ||
      strlen(mnt) >= sizeof(cache->mnt))
    return cache;
  cache = &g_beegfs_ctl_cache[g_beegfs_ctl_cache_n++];
  snprintf(cache->mnt, sizeof(cache->mnt), "%s", mnt);
  return cache;
}

static void beegfs_publish_cached(struct stats *stats, const struct beegfs_ctl_cache *cache)
{
  for (size_t i = 0; i < BEEGFS_CTL_NKEYS; i++) {
    if (cache->state[i] == STATS_SENTINEL_OK)
      stats_set(stats, beegfs_ctl_keys[i], cache->val[i]);
    else
      stats_set_missing(stats, beegfs_ctl_keys[i], (enum stats_sentinel)cache->state[i]);
  }
}

void beegfs_publish_ctl(struct stats *stats, const struct beegfs_ctl_counters *storage,
                        const struct beegfs_ctl_counters *meta)
{
  struct beegfs_ctl_cache scratch;
  struct beegfs_ctl_cache *cache;

  if (stats == NULL)
    return;
  cache = beegfs_ctl_cache_for(stats->s_dev);
  if (cache == NULL)
    cache = &scratch;
  for (size_t i = 0; i < BEEGFS_CTL_NKEYS; i++) {
    unsigned long long v = 0;
    /* Meta is checked first so its close/trunc counts win; storage fills bytes/ops. A column
     * both selected lines omit is 0 (clientstats prints only non-zero columns); with one line
     * missing, an omitted column may belong to the other node type. */
    int ok = (meta != NULL && beegfs_ctl_field(meta, i, &v)) ||
             (storage != NULL && beegfs_ctl_field(storage, i, &v)) ||
             (storage != NULL && meta != NULL);

    cache->val[i] = ok ? v : 0;
    cache->state[i] = ok ? STATS_SENTINEL_OK : STATS_SENTINEL_BACKEND_ERROR;
  }
  beegfs_publish_cached(stats, cache);
}

void beegfs_test_set_mounts_path(const char *path)
{
  g_beegfs_mounts = (path != NULL && path[0] != '\0') ? path : "/proc/mounts";
}

void beegfs_republish_ctl(struct stats *stats)
{
  const struct beegfs_ctl_cache *cache = stats != NULL ? beegfs_ctl_cache_find(stats->s_dev) : NULL;

  if (cache != NULL)
    beegfs_publish_cached(stats, cache);
}

static int beegfs_should_refresh_ctl(void)
{
  time_t now;
  time_t min_iv;

  if (collect_tier_enabled())
    return collect_tier_get_phase() == COLLECT_FULL ? 1 : 0;

  now = time(NULL);
  min_iv = BEEGFS_CTL_MIN_INTERVAL_S;
#ifdef RABBITMQ
  if (sample_freq > (double)min_iv)
    min_iv = (time_t)sample_freq;
#endif
  if (g_beegfs_last_ctl_refresh != 0 && (now - g_beegfs_last_ctl_refresh) < min_iv)
    return 0;
  return 1;
}

static size_t beegfs_collect_local_idents(char idents[][BEEGFS_IDENT_LEN], size_t max_n)
{
  size_t n = 0;
  char host[BEEGFS_IDENT_LEN];
  char *dot;
  struct ifaddrs *ifa_list = NULL;
  struct ifaddrs *ifa;

  if (max_n == 0)
    return 0;

  if (gethostname(host, sizeof(host)) == 0) {
    host[sizeof(host) - 1] = '\0';
    snprintf(idents[n], BEEGFS_IDENT_LEN, "%s", host);
    n++;
    dot = strchr(host, '.');
    if (dot != NULL && dot != host && n < max_n) {
      *dot = '\0';
      snprintf(idents[n], BEEGFS_IDENT_LEN, "%s", host);
      n++;
    }
  }

  if (getifaddrs(&ifa_list) == 0) {
    for (ifa = ifa_list; ifa != NULL && n < max_n; ifa = ifa->ifa_next) {
      char buf[INET_ADDRSTRLEN];
      const struct sockaddr_in *sin;

      if (ifa->ifa_addr == NULL || ifa->ifa_addr->sa_family != AF_INET)
        continue;
      sin = (const struct sockaddr_in *)ifa->ifa_addr;
      if (inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof(buf)) == NULL)
        continue;
      if (strcmp(buf, "127.0.0.1") == 0)
        continue;
      snprintf(idents[n], BEEGFS_IDENT_LEN, "%s", buf);
      n++;
    }
    freeifaddrs(ifa_list);
  }
  return beegfs_idents_add_ib_aliases(idents, n, max_n);
}

static int beegfs_ctl_read_fd(int fd, char *out, size_t out_cap, int timeout_ms)
{
  size_t used = 0;
  time_t deadline = time(NULL) + (timeout_ms + 999) / 1000;

  if (out == NULL || out_cap == 0)
    return -1;
  out[0] = '\0';

  while (used + 1 < out_cap) {
    struct pollfd pfd;
    int pr;
    ssize_t nr;
    int remain_ms;

    remain_ms = (int)((deadline - time(NULL)) * 1000);
    if (remain_ms <= 0)
      return -1;
    pfd.fd = fd;
    pfd.events = POLLIN;
    pr = poll(&pfd, 1, remain_ms);
    if (pr < 0) {
      if (errno == EINTR)
        continue;
      return -1;
    }
    if (pr == 0)
      return -1;
    nr = read(fd, out + used, out_cap - used - 1);
    if (nr < 0) {
      if (errno == EINTR)
        continue;
      return -1;
    }
    if (nr == 0)
      break;
    used += (size_t)nr;
  }
  out[used] = '\0';
  return 0;
}

static int beegfs_ctl_capture(const char *cfgfile, const char *nodetype, int rwunit_b, char *out,
                              size_t out_cap)
{
  int pipefd[2];
  pid_t pid;
  int status = -1;
  struct beegfs_ctl_argv av;

  if (cfgfile == NULL || nodetype == NULL || out == NULL)
    return -1;
  if (beegfs_ctl_build_clientstats_argv(&av, nodetype, cfgfile, rwunit_b) < 0)
    return -1;

  if (pipe(pipefd) != 0)
    return -1;

  pid = fork();
  if (pid < 0) {
    close(pipefd[0]);
    close(pipefd[1]);
    return -1;
  }
  if (pid == 0) {
    close(pipefd[0]);
    if (dup2(pipefd[1], STDOUT_FILENO) < 0)
      _exit(127);
    close(pipefd[1]);
    /* Quiet ctl stderr noise. */
    {
      int nullfd = open("/dev/null", O_WRONLY);
      if (nullfd >= 0) {
        (void)dup2(nullfd, STDERR_FILENO);
        close(nullfd);
      }
    }
    execv(av.argv[0], av.argv);
    _exit(127);
  }

  close(pipefd[1]);
  if (beegfs_ctl_read_fd(pipefd[0], out, out_cap, BEEGFS_CTL_TIMEOUT_MS) != 0) {
    kill(pid, SIGKILL);
    (void)waitpid(pid, &status, 0);
    close(pipefd[0]);
    return -1;
  }
  close(pipefd[0]);
  if (waitpid(pid, &status, 0) < 0)
    return -1;
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
    return -1;
  return 0;
}

static int beegfs_ctl_capture_with_unit(const char *cfgfile, const char *nodetype, char *out,
                                        size_t out_cap)
{
  if (g_beegfs_rwunit_b < 0) {
    if (beegfs_ctl_capture(cfgfile, nodetype, 1, out, out_cap) == 0 &&
        (strstr(out, "[B-rd]") != NULL || strstr(out, "[B-wr]") != NULL ||
         strstr(out, "[ops-rd]") != NULL)) {
      g_beegfs_rwunit_b = 1;
      return 0;
    }
    g_beegfs_rwunit_b = 0;
  }
  return beegfs_ctl_capture(cfgfile, nodetype, g_beegfs_rwunit_b == 1, out, out_cap);
}

static void beegfs_note_ctl_failure(const char *mnt, const char *why)
{
  if (g_beegfs_ctl_fail_latched)
    return;
  g_beegfs_ctl_fail_latched = 1;
  ERROR("beegfs: beegfs-ctl clientstats failed for mount `%s' (%s); "
        "capacity metrics still collected\n",
        mnt != NULL ? mnt : "?", why != NULL ? why : "error");
}

static int beegfs_resolve_cfgfile(const char *mnt_opts, char *out, size_t out_sz)
{
  if (out == NULL || out_sz == 0)
    return -1;
  if (beegfs_cfgfile_from_mnt_opts(mnt_opts, out, out_sz) == 0 && beegfs_path_is_safe(out))
    return 0;
  if (strlen(BEEGFS_CTL_DEFAULT_CFGFILE) + 1 > out_sz)
    return -1;
  snprintf(out, out_sz, "%s", BEEGFS_CTL_DEFAULT_CFGFILE);
  return beegfs_path_is_safe(out) ? 0 : -1;
}

static int beegfs_refresh_ctl_for_mount(struct stats *stats, const char *cfgfile,
                                        const char *const *ident_ptrs, size_t n_idents)
{
  char *buf;
  struct beegfs_ctl_counters storage;
  struct beegfs_ctl_counters meta;
  int storage_ok;
  int meta_ok;

  buf = malloc(BEEGFS_CTL_OUT_MAX);
  if (buf == NULL) {
    beegfs_publish_ctl(stats, NULL, NULL);
    return -1;
  }

  memset(&storage, 0, sizeof(storage));
  memset(&meta, 0, sizeof(meta));

  storage_ok = beegfs_ctl_capture_with_unit(cfgfile, "storage", buf, BEEGFS_CTL_OUT_MAX) == 0 &&
               beegfs_ctl_select_local_line(buf, ident_ptrs, n_idents, &storage);
  meta_ok = beegfs_ctl_capture_with_unit(cfgfile, "meta", buf, BEEGFS_CTL_OUT_MAX) == 0 &&
            beegfs_ctl_select_local_line(buf, ident_ptrs, n_idents, &meta);

  free(buf);
  beegfs_publish_ctl(stats, storage_ok ? &storage : NULL, meta_ok ? &meta : NULL);
  return storage_ok || meta_ok ? 0 : -1;
}

static void beegfs_collect(struct stats_type *type)
{
  const char *me_path = g_beegfs_mounts;
  FILE *me_file = NULL;
  char idents[BEEGFS_MAX_IDENTS][BEEGFS_IDENT_LEN];
  const char *ident_ptrs[BEEGFS_MAX_IDENTS];
  size_t n_idents = 0;
  size_t i;
  int refresh;
  int saw_mount = 0;

  if (type == NULL)
    return;

  if (path_open_is_skipped(me_path)) {
    stats_type_set_missing_all(type, STATS_SENTINEL_READ_FAILED);
    return;
  }
  me_file = setmntent(me_path, "r");
  if (me_file == NULL) {
    stats_type_set_missing_all(type, stats_sentinel_for_errno(errno));
    path_open_record_failure_once(me_path);
    return;
  }

  refresh = beegfs_should_refresh_ctl();
  if (refresh) {
    n_idents = beegfs_collect_local_idents(idents, BEEGFS_MAX_IDENTS);
    for (i = 0; i < n_idents; i++)
      ident_ptrs[i] = idents[i];
  }

  {
    struct mntent me;
    char me_buf[4096];

    while (getmntent_r(me_file, &me, me_buf, sizeof(me_buf)) != NULL) {
      struct stats *stats;

      if (!beegfs_fstype_is_beegfs(me.mnt_type))
        continue;
      if (me.mnt_dir == NULL)
        continue;

      saw_mount = 1;
      stats = get_current_stats(type, me.mnt_dir);
      if (stats == NULL)
        continue;

      beegfs_collect_capacity(stats, me.mnt_dir);

      if (!refresh) {
        beegfs_republish_ctl(stats);
      } else {
        char cfgfile[BEEGFS_CTL_ARGSTR_LEN];

        if (n_idents == 0) {
          beegfs_publish_ctl(stats, NULL, NULL);
          beegfs_note_ctl_failure(me.mnt_dir, "no local host identity");
        } else if (beegfs_resolve_cfgfile(me.mnt_opts, cfgfile, sizeof(cfgfile)) != 0) {
          beegfs_publish_ctl(stats, NULL, NULL);
          beegfs_note_ctl_failure(me.mnt_dir, "missing or unsafe cfgFile");
        } else if (beegfs_refresh_ctl_for_mount(stats, cfgfile, ident_ptrs, n_idents) != 0) {
          beegfs_note_ctl_failure(me.mnt_dir, "missing ctl, timeout, or no local line");
        } else {
          g_beegfs_ctl_fail_latched = 0;
        }
      }
    }
  }

  endmntent(me_file);

  if (refresh && saw_mount)
    g_beegfs_last_ctl_refresh = time(NULL);
}

struct stats_type beegfs_stats_type = {
    .st_name = "beegfs_client",
    .st_collect = &beegfs_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
