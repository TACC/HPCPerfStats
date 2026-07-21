/* stats_buffer_debug_shm.c — overwrite latest schema/@fast/@full payloads in shm (DEBUG). */
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
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"

#define STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE "/dev/shm/hpcperfstatsd-debug"
#define STATS_BUFFER_DEBUG_SHM_ENV_DIR "HPCPERFSTATS_DEBUG_SHM_DIR"
#define STATS_BUFFER_DEBUG_SHM_DIR_MODE 0700
#define STATS_BUFFER_DEBUG_SHM_FILE_MODE 0600

static char g_debug_shm_base_dir_buf[PATH_MAX];
static const char *g_debug_shm_base_dir;

static const char *stats_buffer_debug_shm_base_dir(void)
{
  const char *env;

  if (g_debug_shm_base_dir != NULL)
    return g_debug_shm_base_dir;
  env = getenv(STATS_BUFFER_DEBUG_SHM_ENV_DIR);
  if (env != NULL && env[0] != '\0') {
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

  if (mkdir(base, STATS_BUFFER_DEBUG_SHM_DIR_MODE) < 0 && errno != EEXIST) {
    monitor_log_debug("debug_shm: mkdir %s: %m\n", base);
    return -1;
  }
  return 0;
}

void stats_buffer_debug_shm_init(void)
{
  (void)stats_buffer_debug_shm_ensure_dir();
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

  fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, STATS_BUFFER_DEBUG_SHM_FILE_MODE);
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

void stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                          enum stats_buffer_debug_shm_payload_kind kind)
{
  char final_path[PATH_MAX];
  char tmp_path[PATH_MAX];
  const char *base;
  const char *name;

  if (sf == NULL || sf->sf_data == NULL || sf->sf_data_len == 0)
    return;

  if (stats_buffer_debug_shm_ensure_dir() < 0)
    return;

  base = stats_buffer_debug_shm_base_dir();
  name = stats_buffer_debug_shm_kind_name(kind);
  if (snprintf(final_path, sizeof(final_path), "%s/%s", base, name) >= (int)sizeof(final_path))
    return;
  if (snprintf(tmp_path, sizeof(tmp_path), "%s/%s.tmp", base, name) >= (int)sizeof(tmp_path))
    return;
  if (stats_buffer_debug_shm_write_atomic(final_path, tmp_path, sf->sf_data, sf->sf_data_len) < 0)
    monitor_log_debug("debug_shm: write %s: %m\n", final_path);
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

#endif /* DEBUG */
