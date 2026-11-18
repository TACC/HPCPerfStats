/* beegfs_client - BeeGFS mount detection, statvfs capacity, and I/O bytes from the kernel TCP
 * counters on the client's storage connections (no server query). */
#include <dirent.h>
#include <errno.h>
#include <mntent.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>
#include <time.h>

#include "beegfs_client.h"
#include "beegfs_mount.h"
#include "collect_tier.h"
#include "path_open_fail_once.h"
#include "stats.h"
#include "trace.h"

#ifdef RABBITMQ
#include "monitor_daemon.h"
#endif

#define BEEGFS_IO_MIN_INTERVAL_S 30
#define BEEGFS_MAX_MOUNTS 32
#define BEEGFS_PATH_LEN 512
#define BEEGFS_NODES_MAX 65536

static time_t g_beegfs_last_io_refresh;
static const char *g_beegfs_mounts = "/proc/mounts";
static const char *g_beegfs_procfs = "/proc/fs/beegfs";

static const char *const beegfs_fs_keys[] = {"fs_bytes_total", "fs_bytes_free", "fs_bytes_avail",
                                             "fs_files_total", "fs_files_free"};

/* The client keeps no per-operation counters, and the only other source (beegfs-ctl
 * --clientstats) makes every server dump its whole client table per node, so these stay
 * NOT_PRESENT. */
static const char *const beegfs_op_keys[] = {
    "vfs_read_ops",    "vfs_write_ops",    "vfs_open_ops",    "vfs_close_ops",  "vfs_getattr_ops",
    "vfs_setattr_ops", "vfs_truncate_ops", "vfs_readdir_ops", "vfs_create_ops", "vfs_mkdir_ops",
    "vfs_rmdir_ops",   "vfs_rename_ops",   "vfs_unlink_ops",  "vfs_link_ops",   "vfs_statfs_ops"};

/* What the last I/O refresh published for one mount, re-published on ticks the refresh gate
 * skips. A mount beyond BEEGFS_MAX_MOUNTS has no cache and emits NOT_UPDATED bytes. */
struct beegfs_io_cache {
  char mnt[BEEGFS_PATH_LEN];
  unsigned char state;
  struct beegfs_tcp_acc *acc;
};
static struct beegfs_io_cache g_beegfs_io_cache[BEEGFS_MAX_MOUNTS];
static size_t g_beegfs_io_cache_n;

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

static struct beegfs_io_cache *beegfs_io_cache_find(const char *mnt)
{
  for (size_t i = 0; i < g_beegfs_io_cache_n; i++)
    if (strcmp(g_beegfs_io_cache[i].mnt, mnt) == 0)
      return &g_beegfs_io_cache[i];
  return NULL;
}

/* The cache slot of `mnt`, added on first use; NULL when the table is full. */
static struct beegfs_io_cache *beegfs_io_cache_for(const char *mnt)
{
  struct beegfs_io_cache *cache = beegfs_io_cache_find(mnt);

  if (cache != NULL || g_beegfs_io_cache_n == BEEGFS_MAX_MOUNTS ||
      strlen(mnt) >= sizeof(cache->mnt))
    return cache;
  cache = &g_beegfs_io_cache[g_beegfs_io_cache_n++];
  snprintf(cache->mnt, sizeof(cache->mnt), "%s", mnt);
  cache->state = STATS_SENTINEL_NOT_UPDATED;
  return cache;
}

static void beegfs_publish_bytes(struct stats *stats, enum stats_sentinel code,
                                 const struct beegfs_tcp_acc *acc)
{
  for (size_t i = 0; i < sizeof(beegfs_op_keys) / sizeof(beegfs_op_keys[0]); i++)
    stats_set_missing(stats, beegfs_op_keys[i], STATS_SENTINEL_NOT_PRESENT);
  if (code == STATS_SENTINEL_OK && acc != NULL) {
    stats_set(stats, "vfs_write_bytes", acc->tx);
    stats_set(stats, "vfs_read_bytes", acc->rx);
  } else {
    stats_set_missing(stats, "vfs_write_bytes", code);
    stats_set_missing(stats, "vfs_read_bytes", code);
  }
}

void beegfs_publish_io(struct stats *stats, const struct beegfs_tcp_sock *socks, size_t n,
                       enum stats_sentinel code)
{
  struct beegfs_io_cache *cache;

  if (stats == NULL)
    return;
  cache = beegfs_io_cache_for(stats->s_dev);
  if (cache == NULL) {
    beegfs_publish_bytes(stats, STATS_SENTINEL_NOT_UPDATED, NULL);
    return;
  }
  if (code == STATS_SENTINEL_OK && cache->acc == NULL &&
      (cache->acc = calloc(1, sizeof(*cache->acc))) == NULL)
    code = STATS_SENTINEL_READ_FAILED;
  if (code == STATS_SENTINEL_OK)
    beegfs_tcp_acc_update(cache->acc, socks, n);
  cache->state = (unsigned char)code;
  beegfs_publish_bytes(stats, code, cache->acc);
}

void beegfs_republish_io(struct stats *stats)
{
  const struct beegfs_io_cache *cache = stats != NULL ? beegfs_io_cache_find(stats->s_dev) : NULL;

  if (cache != NULL && cache->state != STATS_SENTINEL_NOT_UPDATED)
    beegfs_publish_bytes(stats, (enum stats_sentinel)cache->state, cache->acc);
}

void beegfs_test_set_mounts_path(const char *path)
{
  g_beegfs_mounts = (path != NULL && path[0] != '\0') ? path : "/proc/mounts";
}

void beegfs_test_set_procfs_root(const char *path)
{
  g_beegfs_procfs = (path != NULL && path[0] != '\0') ? path : "/proc/fs/beegfs";
}

static int beegfs_should_refresh_io(void)
{
  time_t now;
  time_t min_iv;

  if (collect_tier_enabled())
    return collect_tier_get_phase() == COLLECT_FULL ? 1 : 0;

  now = time(NULL);
  min_iv = BEEGFS_IO_MIN_INTERVAL_S;
#ifdef RABBITMQ
  if (sample_freq > (double)min_iv)
    min_iv = (time_t)sample_freq;
#endif
  if (g_beegfs_last_io_refresh != 0 && (now - g_beegfs_last_io_refresh) < min_iv)
    return 0;
  return 1;
}

/* 1 when client instance dir `dir` has `cfgFile = <cfgfile>` in its config. */
static int beegfs_client_uses_cfgfile(const char *dir, const char *cfgfile)
{
  char path[BEEGFS_PATH_LEN + 16];
  char line[BEEGFS_PATH_LEN + 32];
  char val[BEEGFS_PATH_LEN];
  int match = 0;
  FILE *f;

  snprintf(path, sizeof(path), "%s/config", dir);
  f = fopen(path, "re");
  if (f == NULL)
    return 0;
  while (!match && fgets(line, sizeof(line), f) != NULL)
    match = sscanf(line, "cfgFile = %511s", val) == 1 && strcmp(val, cfgfile) == 0;
  fclose(f);
  return match;
}

/* The client instance dir whose config names `cfgfile` (NULL: the mount gave none); without a
 * match, the only instance when there is exactly one. @return 0 found, -1 none */
static int beegfs_client_dir(const char *cfgfile, char *out, size_t out_sz)
{
  char only[BEEGFS_PATH_LEN];
  int n = 0;
  int found = 0;
  struct dirent *e;
  DIR *d = opendir(g_beegfs_procfs);

  if (d == NULL)
    return -1;
  while (!found && (e = readdir(d)) != NULL) {
    if (e->d_name[0] == '.')
      continue;
    snprintf(out, out_sz, "%s/%s", g_beegfs_procfs, e->d_name);
    if (++n == 1)
      snprintf(only, sizeof(only), "%s", out);
    found = cfgfile != NULL && beegfs_client_uses_cfgfile(out, cfgfile);
  }
  closedir(d);
  if (found)
    return 0;
  if (n != 1)
    return -1;
  snprintf(out, out_sz, "%s", only);
  return 0;
}

/* Read the storage-server listing of client instance dir `dir` into `peers`.
 * @return peer count, or -1 when the TCP counters cannot account for the client's storage
 * traffic (listing unreadable, RDMA/SDP connections, ...) */
static long beegfs_storage_peers(const char *dir, struct beegfs_tcp_peer *peers, size_t max)
{
  static char text[BEEGFS_NODES_MAX];
  char path[BEEGFS_PATH_LEN + 16];
  size_t len;
  size_t n;
  int untracked;
  FILE *f;

  snprintf(path, sizeof(path), "%s/storage_nodes", dir);
  f = fopen(path, "re");
  if (f == NULL)
    return -1;
  len = fread(text, 1, sizeof(text) - 1, f);
  fclose(f);
  text[len] = '\0';
  n = beegfs_tcp_parse_peers(text, peers, max, &untracked);
  return untracked || len == sizeof(text) - 1 ? -1 : (long)n;
}

/* Refresh mount row `stats` from the TCP counters of its client's storage connections. */
static void beegfs_refresh_io(struct stats *stats, const char *mnt_opts)
{
  static struct beegfs_tcp_sock socks[BEEGFS_TCP_MAX_SOCKS];
  struct beegfs_tcp_peer peers[BEEGFS_TCP_MAX_PEERS];
  char cfgfile[BEEGFS_PATH_LEN];
  char dir[BEEGFS_PATH_LEN];
  int have_cfg = beegfs_cfgfile_from_mnt_opts(mnt_opts, cfgfile, sizeof(cfgfile)) == 0;
  long npeers = -1;
  size_t nsocks = 0;
  size_t listed = 0;
  enum stats_sentinel code = STATS_SENTINEL_OK;

  if (beegfs_client_dir(have_cfg ? cfgfile : NULL, dir, sizeof(dir)) == 0)
    npeers = beegfs_storage_peers(dir, peers, BEEGFS_TCP_MAX_PEERS);
  for (long i = 0; i < npeers; i++)
    listed += peers[i].conns;
  if (npeers < 0)
    code = STATS_SENTINEL_NOT_PRESENT;
  else if (npeers > 0 &&
           beegfs_tcp_dump(peers, (size_t)npeers, socks, BEEGFS_TCP_MAX_SOCKS, &nsocks) != 0) {
    code = stats_sentinel_for_errno(errno);
    TRACE("beegfs: TCP counter read failed for %s: %m\n", stats->s_dev);
  } else if (nsocks < listed) {
    /* The listing names one address per server; connections to its other addresses are not
     * matched, so the total would be short. */
    code = STATS_SENTINEL_NOT_PRESENT;
    TRACE("beegfs: %zu of %zu storage connections of %s matched\n", nsocks, listed, stats->s_dev);
  }
  beegfs_publish_io(stats, socks, nsocks, code);
}

static void beegfs_collect(struct stats_type *type)
{
  const char *me_path = g_beegfs_mounts;
  FILE *me_file = NULL;
  struct mntent me;
  char me_buf[4096];
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

  refresh = beegfs_should_refresh_io();
  while (getmntent_r(me_file, &me, me_buf, sizeof(me_buf)) != NULL) {
    struct stats *stats;

    if (!beegfs_fstype_is_beegfs(me.mnt_type) || me.mnt_dir == NULL)
      continue;
    saw_mount = 1;
    stats = get_current_stats(type, me.mnt_dir);
    if (stats == NULL)
      continue;
    beegfs_collect_capacity(stats, me.mnt_dir);
    if (refresh)
      beegfs_refresh_io(stats, me.mnt_opts);
    else
      beegfs_republish_io(stats);
  }
  endmntent(me_file);

  if (refresh && saw_mount)
    g_beegfs_last_io_refresh = time(NULL);
}

struct stats_type beegfs_stats_type = {
    .st_name = "beegfs_client",
    .st_collect = &beegfs_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
