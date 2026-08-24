/* beegfs_client — BeeGFS mount detection, statvfs capacity, bounded beegfs-ctl I/O. */
#include <arpa/inet.h>
#include <ctype.h>
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

static time_t g_beegfs_last_ctl_refresh;
static int g_beegfs_ctl_fail_latched;
static int g_beegfs_rwunit_b = -1; /* -1 unknown, 0 MiB, 1 bytes */

static void beegfs_collect_capacity(struct stats *stats, const char *mnt)
{
  struct statvfs sv;

  if (stats == NULL || mnt == NULL)
    return;
  if (statvfs(mnt, &sv) != 0) {
    TRACE("beegfs: statvfs(%s) failed: %m\n", mnt);
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

static void beegfs_apply_counters(struct stats *stats, const struct beegfs_ctl_counters *c)
{
  if (stats == NULL || c == NULL)
    return;
#define SET_IF(field, key)                                                                         \
  do {                                                                                             \
    if ((c)->have_##field)                                                                         \
      stats_set(stats, (key), (c)->field);                                                         \
  } while (0)
  SET_IF(vfs_read_bytes, "vfs_read_bytes");
  SET_IF(vfs_write_bytes, "vfs_write_bytes");
  SET_IF(vfs_read_ops, "vfs_read_ops");
  SET_IF(vfs_write_ops, "vfs_write_ops");
  SET_IF(vfs_open_ops, "vfs_open_ops");
  SET_IF(vfs_close_ops, "vfs_close_ops");
  SET_IF(vfs_getattr_ops, "vfs_getattr_ops");
  SET_IF(vfs_setattr_ops, "vfs_setattr_ops");
  SET_IF(vfs_truncate_ops, "vfs_truncate_ops");
  SET_IF(vfs_readdir_ops, "vfs_readdir_ops");
  SET_IF(vfs_create_ops, "vfs_create_ops");
  SET_IF(vfs_mkdir_ops, "vfs_mkdir_ops");
  SET_IF(vfs_rmdir_ops, "vfs_rmdir_ops");
  SET_IF(vfs_rename_ops, "vfs_rename_ops");
  SET_IF(vfs_unlink_ops, "vfs_unlink_ops");
  SET_IF(vfs_link_ops, "vfs_link_ops");
  SET_IF(vfs_statfs_ops, "vfs_statfs_ops");
#undef SET_IF
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
    execvp(av.argv[0], av.argv);
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

static int beegfs_refresh_ctl_for_mount(struct stats *stats, const char *mnt, const char *cfgfile,
                                        const char *const *ident_ptrs, size_t n_idents)
{
  char *buf;
  struct beegfs_ctl_counters storage;
  struct beegfs_ctl_counters meta;
  int ok = 0;

  (void)mnt;
  buf = malloc(BEEGFS_CTL_OUT_MAX);
  if (buf == NULL)
    return -1;

  memset(&storage, 0, sizeof(storage));
  memset(&meta, 0, sizeof(meta));

  if (beegfs_ctl_capture_with_unit(cfgfile, "storage", buf, BEEGFS_CTL_OUT_MAX) == 0) {
    if (beegfs_ctl_select_local_line(buf, ident_ptrs, n_idents, &storage))
      ok = 1;
  }
  if (beegfs_ctl_capture_with_unit(cfgfile, "meta", buf, BEEGFS_CTL_OUT_MAX) == 0) {
    if (beegfs_ctl_select_local_line(buf, ident_ptrs, n_idents, &meta))
      ok = 1;
  }

  free(buf);
  if (!ok)
    return -1;

  /* Prefer meta close/trunc when both present (plan). Storage fills bytes/ops. */
  beegfs_apply_counters(stats, &storage);
  beegfs_apply_counters(stats, &meta);
  return 0;
}

static void beegfs_collect(struct stats_type *type)
{
  const char *me_path = "/proc/mounts";
  FILE *me_file = NULL;
  char idents[BEEGFS_MAX_IDENTS][BEEGFS_IDENT_LEN];
  const char *ident_ptrs[BEEGFS_MAX_IDENTS];
  size_t n_idents = 0;
  size_t i;
  int refresh;
  int saw_mount = 0;

  if (type == NULL)
    return;

  if (path_open_is_skipped(me_path))
    return;
  me_file = setmntent(me_path, "r");
  if (me_file == NULL) {
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

      if (refresh && n_idents > 0) {
        char cfgfile[BEEGFS_CTL_ARGSTR_LEN];

        if (beegfs_resolve_cfgfile(me.mnt_opts, cfgfile, sizeof(cfgfile)) != 0) {
          beegfs_note_ctl_failure(me.mnt_dir, "missing or unsafe cfgFile");
        } else if (beegfs_refresh_ctl_for_mount(stats, me.mnt_dir, cfgfile, ident_ptrs, n_idents) !=
                   0) {
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
