/*
 * stats_buffer_debug_shm schema mirror and gating tests.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"

static char g_shm_base[256];

static int setup_shm_dir(void)
{
  const char *tmpdir = getenv("TMPDIR");

  if (tmpdir == NULL || tmpdir[0] == '\0')
    tmpdir = "/tmp";
  snprintf(g_shm_base, sizeof(g_shm_base), "%s/hpcperfstats_schema_XXXXXX", tmpdir);
  if (mkdtemp(g_shm_base) == NULL)
    return -1;
  if (setenv("HPCPERFSTATS_DEBUG_SHM_DIR", g_shm_base, 1) != 0)
    return -1;
  stats_buffer_debug_shm_init();
  return 0;
}

static void test_schema_wanted_gate(void)
{
  assert(stats_buffer_debug_shm_schema_wanted(1, 1) == 1);
  assert(stats_buffer_debug_shm_schema_wanted(1, 0) == 0);
  assert(stats_buffer_debug_shm_schema_wanted(0, 1) == 0);
  assert(stats_buffer_debug_shm_sample_wanted(0, 1) == 1);
  assert(stats_buffer_debug_shm_sample_wanted(1, 1) == 0);
}

#ifdef DEBUG

static void test_schema_payload_written(void)
{
  struct stats_buffer sf;
  char schema_path[512];
  char fast_path[512];
  char buf[1024];
  struct stat st;

  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("$hpcperfstats 3.0\n$hostname testhost\n");
  assert(sf.sf_data != NULL);
  sf.sf_data_len = strlen(sf.sf_data);
  sf.sf_data_cap = sf.sf_data_len + 1;

  stats_buffer_debug_shm_write_payload(&sf, STATS_BUFFER_DEBUG_SHM_PAYLOAD_SCHEMA);

  snprintf(schema_path, sizeof(schema_path), "%s/schema", g_shm_base);
  snprintf(fast_path, sizeof(fast_path), "%s/fast", g_shm_base);
  assert(stat(schema_path, &st) == 0);
  assert(stat(fast_path, &st) != 0);

  {
    FILE *f = fopen(schema_path, "r");
    assert(f != NULL);
    assert(fgets(buf, (int) sizeof(buf), f) != NULL);
    fclose(f);
  }
  assert(strncmp(buf, "$hpcperfstats", 13) == 0);

  free(sf.sf_data);
}

#endif /* DEBUG */

int main(void)
{
  test_schema_wanted_gate();
#ifndef DEBUG
  printf("test_debug_shm_schema_mirror: schema write skipped (not DEBUG)\n");
  return 0;
#else
  assert(setup_shm_dir() == 0);
  test_schema_payload_written();
  printf("test_debug_shm_schema_mirror passed\n");
  return 0;
#endif
}
