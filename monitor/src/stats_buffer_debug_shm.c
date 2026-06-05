/* stats_buffer_debug_shm.c — overwrite latest @fast/@full samples in shm (DEBUG). */
#ifdef DEBUG

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "fileio.h"
#include "monitor_log.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"

#define STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE "/dev/shm/hpcperfstatsd-debug"
#define STATS_BUFFER_DEBUG_SHM_ENV_DIR "HPCPERFSTATS_DEBUG_SHM_DIR"

static const char *g_debug_shm_base_dir;

static const char *stats_buffer_debug_shm_base_dir(void)
{
  const char *env;

  if (g_debug_shm_base_dir != NULL)
    return g_debug_shm_base_dir;
  env = getenv(STATS_BUFFER_DEBUG_SHM_ENV_DIR);
  if (env != NULL && env[0] != '\0')
    g_debug_shm_base_dir = env;
  else
    g_debug_shm_base_dir = STATS_BUFFER_DEBUG_SHM_DEFAULT_BASE;
  return g_debug_shm_base_dir;
}

void stats_buffer_debug_shm_init(void)
{
  const char *base = stats_buffer_debug_shm_base_dir();

  if (mkdir(base, 0755) < 0 && errno != EEXIST)
    monitor_log_debug("debug_shm: mkdir %s: %m\n", base);
}

static const char *stats_buffer_debug_shm_tier_name(enum stats_row_tier tier)
{
  if (tier == STATS_ROW_FAST)
    return "fast";
  return "full";
}

static int stats_buffer_debug_shm_write_atomic(const char *final_path,
					       const char *tmp_path,
					       const void *data, size_t len)
{
  int fd;
  size_t off = 0;

  fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
  if (fd < 0)
    return -1;
  while (off < len) {
    ssize_t n = write(fd, (const char *) data + off, len - off);

    if (n < 0) {
      close(fd);
      unlink(tmp_path);
      return -1;
    }
    off += (size_t) n;
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

void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
					 enum stats_row_tier tier)
{
  char final_path[PATH_MAX];
  char tmp_path[PATH_MAX];
  const char *base;
  const char *name;

  if (sf == NULL || sf->sf_data == NULL || sf->sf_data_len == 0)
    return;

  base = stats_buffer_debug_shm_base_dir();
  name = stats_buffer_debug_shm_tier_name(tier);
  if (snprintf(final_path, sizeof(final_path), "%s/%s", base, name)
      >= (int) sizeof(final_path))
    return;
  if (snprintf(tmp_path, sizeof(tmp_path), "%s/%s.tmp", base, name)
      >= (int) sizeof(tmp_path))
    return;
  (void) stats_buffer_debug_shm_write_atomic(final_path, tmp_path,
					     sf->sf_data, sf->sf_data_len);
}

#endif /* DEBUG */
