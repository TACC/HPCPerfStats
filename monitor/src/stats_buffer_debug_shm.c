/* Overwrite latest schema/@fast/@full payloads in shm (DEBUG). */
#ifdef DEBUG

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "monitor_log.h"
#include "secure_path.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"

#define STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE "/dev/shm/hpcperfstatsd-debug"
#define STATS_BUFFER_DEBUG_SHM_ENV_DIR "HPCPERFSTATS_DEBUG_SHM_DIR"
#define STATS_BUFFER_DEBUG_SHM_DIR_MODE 0700
#define STATS_BUFFER_DEBUG_SHM_FILE_MODE 0600
/* Cap in-memory burst (N jid + node-wide payloads for one sample tick). */
#define STATS_BUFFER_DEBUG_SHM_BURST_MAX (4u * 1024u * 1024u)
#define STATS_BUFFER_DEBUG_SHM_BURST_SEP "\n#---burst-part---\n"

static char g_debug_shm_base_dir_buf[PATH_MAX];
static const char *g_debug_shm_base_dir;
static char *g_burst_data;
static size_t g_burst_len;
static size_t g_burst_cap;
static int g_burst_open;
static int g_burst_truncated;

static int stats_buffer_debug_shm_env_allowed(const char *env)
{
  if (env == NULL || env[0] != '/')
    return 0;
  if (strstr(env, "/../") != NULL)
    return 0;
  if (strncmp(env, "/dev/shm/", 9) != 0)
    return 0;
  return 1;
}

static const char *stats_buffer_debug_shm_base_dir(void)
{
  const char *env;

  if (g_debug_shm_base_dir != NULL)
    return g_debug_shm_base_dir;
  env = getenv(STATS_BUFFER_DEBUG_SHM_ENV_DIR);
  if (env != NULL && env[0] != '\0' && stats_buffer_debug_shm_env_allowed(env)) {
    if (snprintf(g_debug_shm_base_dir_buf, sizeof(g_debug_shm_base_dir_buf), "%s", env) >=
        (int)sizeof(g_debug_shm_base_dir_buf))
      g_debug_shm_base_dir = STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE;
    else
      g_debug_shm_base_dir = g_debug_shm_base_dir_buf;
  } else {
    g_debug_shm_base_dir = STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE;
  }
  return g_debug_shm_base_dir;
}

/* Recreate base dir if missing (e.g. operator rm -rf /dev/shm/... mid-run). */
static int stats_buffer_debug_shm_ensure_dir(void)
{
  const char *base = stats_buffer_debug_shm_base_dir();

  if (ensure_private_dir(base, STATS_BUFFER_DEBUG_SHM_DIR_MODE) < 0) {
    monitor_log_debug("debug_shm: ensure_private_dir %s: %m\n", base);
    return -1;
  }
  return 0;
}

/* The previous daemon's samples would otherwise sit beside the new schema until the first tick.
 * schema, sentinels and sentinel_sites stay; the startup header payload rewrites them. */
void stats_buffer_debug_shm_init(void)
{
  static const char *const stale[] = {"fast", "full", "fast.burst", "full.burst"};
  char path[PATH_MAX];
  size_t i;

  if (stats_buffer_debug_shm_ensure_dir() < 0)
    return;
  for (i = 0; i < sizeof(stale) / sizeof(stale[0]); i++)
    if (snprintf(path, sizeof(path), "%s/%s", stats_buffer_debug_shm_base_dir(), stale[i]) <
            (int)sizeof(path) &&
        unlink(path) < 0 && errno != ENOENT)
      monitor_log_debug("debug_shm: unlink %s: %m\n", path);
}

static const char *stats_buffer_debug_shm_kind_name(enum stats_buffer_debug_shm_payload_kind kind)
{
  switch (kind) {
  case STATS_BUFFER_DEBUG_SHM_PAYLOAD_SCHEMA:
    return "schema";
  case STATS_BUFFER_DEBUG_SHM_PAYLOAD_FAST:
    return "fast";
  case STATS_BUFFER_DEBUG_SHM_PAYLOAD_FULL:
    return "full";
  default:
    return "full";
  }
}

static int stats_buffer_debug_shm_write_atomic(const char *final_path, const char *tmp_path,
                                               const void *data, size_t len)
{
  int fd;
  size_t off = 0;

  fd = open(tmp_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
            STATS_BUFFER_DEBUG_SHM_FILE_MODE);
  if (fd < 0)
    return -1;
  while (off < len) {
    ssize_t n = write(fd, (const char *)data + off, len - off);

    if (n < 0) {
      close(fd);
      unlink(tmp_path);
      return -1;
    }
    off += (size_t)n;
  }
  if (close(fd) < 0) {
    unlink(tmp_path);
    return -1;
  }
  if (rename(tmp_path, final_path) < 0) {
    unlink(tmp_path);
    return -1;
  }
  return 0;
}

void stats_buffer_debug_shm_write_file(const char *name, const void *data, size_t len)
{
  char final_path[PATH_MAX];
  char tmp_path[PATH_MAX];
  const char *base;

  if (name == NULL || (data == NULL && len != 0))
    return;
  if (stats_buffer_debug_shm_ensure_dir() < 0)
    return;

  base = stats_buffer_debug_shm_base_dir();
  if (snprintf(final_path, sizeof(final_path), "%s/%s", base, name) >= (int)sizeof(final_path))
    return;
  if (snprintf(tmp_path, sizeof(tmp_path), "%s/%s.tmp", base, name) >= (int)sizeof(tmp_path))
    return;
  unlink(tmp_path); /* clear stale tmp from prior crash so O_EXCL can succeed */
  if (stats_buffer_debug_shm_write_atomic(final_path, tmp_path, data, len) < 0)
    monitor_log_debug("debug_shm: write %s: %m\n", final_path);
}

void stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                          enum stats_buffer_debug_shm_payload_kind kind)
{
  if (sf == NULL || sf->sf_data == NULL || sf->sf_data_len == 0)
    return;
  stats_buffer_debug_shm_write_file(stats_buffer_debug_shm_kind_name(kind), sf->sf_data,
                                    sf->sf_data_len);
}

void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf, enum stats_row_tier tier)
{
  enum stats_buffer_debug_shm_payload_kind kind;

  if (tier == STATS_ROW_FAST)
    kind = STATS_BUFFER_DEBUG_SHM_PAYLOAD_FAST;
  else
    kind = STATS_BUFFER_DEBUG_SHM_PAYLOAD_FULL;
  stats_buffer_debug_shm_write_payload(sf, kind);
}

static void stats_buffer_debug_shm_burst_clear(void)
{
  free(g_burst_data);
  g_burst_data = NULL;
  g_burst_len = 0;
  g_burst_cap = 0;
  g_burst_open = 0;
  g_burst_truncated = 0;
}

void stats_buffer_debug_shm_burst_begin(void)
{
  stats_buffer_debug_shm_burst_clear();
  g_burst_open = 1;
}

static int stats_buffer_debug_shm_burst_reserve(size_t add)
{
  size_t need;
  size_t ncap;
  char *nd;

  if (add > STATS_BUFFER_DEBUG_SHM_BURST_MAX ||
      g_burst_len > STATS_BUFFER_DEBUG_SHM_BURST_MAX - add) {
    g_burst_truncated = 1;
    return -1;
  }
  need = g_burst_len + add;
  if (need <= g_burst_cap)
    return 0;
  ncap = g_burst_cap ? g_burst_cap : 4096;
  while (ncap < need) {
    if (ncap > STATS_BUFFER_DEBUG_SHM_BURST_MAX / 2)
      ncap = STATS_BUFFER_DEBUG_SHM_BURST_MAX;
    else
      ncap *= 2;
  }
  if (ncap > STATS_BUFFER_DEBUG_SHM_BURST_MAX)
    ncap = STATS_BUFFER_DEBUG_SHM_BURST_MAX;
  if (need > ncap) {
    g_burst_truncated = 1;
    return -1;
  }
  nd = realloc(g_burst_data, ncap);
  if (nd == NULL) {
    g_burst_truncated = 1;
    return -1;
  }
  g_burst_data = nd;
  g_burst_cap = ncap;
  return 0;
}

void stats_buffer_debug_shm_burst_append(const struct stats_buffer *sf)
{
  size_t sep_len = 0;
  size_t add;

  if (!g_burst_open || sf == NULL || sf->sf_data == NULL || sf->sf_data_len == 0)
    return;
  if (g_burst_truncated)
    return;

  if (g_burst_len > 0)
    sep_len = strlen(STATS_BUFFER_DEBUG_SHM_BURST_SEP);

  add = sep_len + sf->sf_data_len;
  if (stats_buffer_debug_shm_burst_reserve(add) < 0)
    return;
  if (sep_len > 0) {
    memcpy(g_burst_data + g_burst_len, STATS_BUFFER_DEBUG_SHM_BURST_SEP, sep_len);
    g_burst_len += sep_len;
  }
  memcpy(g_burst_data + g_burst_len, sf->sf_data, sf->sf_data_len);
  g_burst_len += sf->sf_data_len;
}

void stats_buffer_debug_shm_burst_commit(enum stats_row_tier tier)
{
  char final_path[PATH_MAX];
  char tmp_path[PATH_MAX];
  const char *base;
  const char *name;
  const char trunc_note[] = "\n#---burst-truncated---\n";

  if (!g_burst_open || g_burst_len == 0) {
    stats_buffer_debug_shm_burst_clear();
    return;
  }

  if (g_burst_truncated && stats_buffer_debug_shm_burst_reserve(sizeof(trunc_note) - 1) == 0) {
    memcpy(g_burst_data + g_burst_len, trunc_note, sizeof(trunc_note) - 1);
    g_burst_len += sizeof(trunc_note) - 1;
  }

  if (stats_buffer_debug_shm_ensure_dir() < 0) {
    stats_buffer_debug_shm_burst_clear();
    return;
  }

  base = stats_buffer_debug_shm_base_dir();
  name = (tier == STATS_ROW_FAST) ? "fast.burst" : "full.burst";
  if (snprintf(final_path, sizeof(final_path), "%s/%s", base, name) >= (int)sizeof(final_path)) {
    stats_buffer_debug_shm_burst_clear();
    return;
  }
  if (snprintf(tmp_path, sizeof(tmp_path), "%s/%s.tmp", base, name) >= (int)sizeof(tmp_path)) {
    stats_buffer_debug_shm_burst_clear();
    return;
  }
  unlink(tmp_path);
  if (stats_buffer_debug_shm_write_atomic(final_path, tmp_path, g_burst_data, g_burst_len) < 0)
    monitor_log_debug("debug_shm: write %s: %m\n", final_path);
  stats_buffer_debug_shm_burst_clear();
}

#endif /* DEBUG */
