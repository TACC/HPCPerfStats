/*
 * stats_buffer_debug_shm.c unit tests (DEBUG builds; no-op stub path when !DEBUG).
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "collect_tier.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"
#include "test_stats_buffer_collect_stubs.h"

char jobid[80] = "job42";
double send_freq = 1.0;
int nr_cpus = 1;
int n_pmcs = 0;
processor_t processor = (processor_t)0;

int stats_buffer_test_send_hook(struct stats_buffer *sf)
{
  (void)sf;
  return 0;
}

static char g_shm_base[256];

static int setup_shm_dir(void)
{
  const char *tmpdir = getenv("TMPDIR");

  if (tmpdir == NULL || tmpdir[0] == '\0')
    tmpdir = "/tmp";
  snprintf(g_shm_base, sizeof(g_shm_base), "%s/hpcperfstats_debug_XXXXXX", tmpdir);
  if (mkdtemp(g_shm_base) == NULL)
    return -1;
  if (setenv("HPCPERFSTATS_DEBUG_SHM_DIR", g_shm_base, 1) != 0)
    return -1;
  stats_buffer_debug_shm_init();
  return 0;
}

static int path_exists(const char *path)
{
  struct stat st;

  return stat(path, &st) == 0;
}

static int read_file_text(const char *path, char *buf, size_t cap)
{
  FILE *f;
  size_t n;

  f = fopen(path, "r");
  if (f == NULL)
    return -1;
  n = fread(buf, 1, cap - 1, f);
  buf[n] = '\0';
  fclose(f);
  return (int)n;
}

static void build_collect_payload(struct stats_buffer *sf, enum collect_phase phase)
{
  collect_tier_set_enabled(1);
  collect_tier_set_phase(phase);
  memset(sf, 0, sizeof(*sf));
  assert(stats_buffer_open(sf, "127.0.0.1", "5672", "q", "u", "p") == 0);
  free(sf->sf_data);
  sf->sf_data = strdup("");
  assert(sf->sf_data != NULL);
  sf->sf_data_cap = 1;
  sf->sf_data_len = 0;
  assert(stats_buffer_collect(sf) == 0);
}

#ifdef DEBUG

static void test_fast_writes_fast_only(void)
{
  struct stats_buffer_collect_fixture fx;
  struct stats_buffer sf;
  char fast_path[512];
  char full_path[512];
  char buf[1024];
  const unsigned long long vals[2] = {11, 22};

  assert(stats_buffer_collect_fixture_init(&fx, "a,E b,E,R=S", vals, 2) == 0);
  build_collect_payload(&sf, COLLECT_FAST_ONLY);
  assert(stats_buffer_payload_row_tier(&sf) == STATS_ROW_FAST);
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_FAST);

  snprintf(fast_path, sizeof(fast_path), "%s/fast", g_shm_base);
  snprintf(full_path, sizeof(full_path), "%s/full", g_shm_base);
  assert(path_exists(fast_path));
  assert(!path_exists(full_path));
  assert(read_file_text(fast_path, buf, sizeof(buf)) > 0);
  assert(strstr(buf, "@fast") != NULL);
  assert(strstr(buf, "job42") != NULL);

  stats_buffer_close(&sf);
  stats_buffer_collect_fixture_teardown(&fx);
}

static void test_full_writes_full_only(void)
{
  struct stats_buffer_collect_fixture fx;
  struct stats_buffer sf;
  char full_path[512];
  char buf[1024];
  const unsigned long long vals[2] = {11, 22};

  assert(stats_buffer_collect_fixture_init(&fx, "a,E b,E,R=S", vals, 2) == 0);
  build_collect_payload(&sf, COLLECT_FULL);
  assert(stats_buffer_payload_row_tier(&sf) == STATS_ROW_FULL);
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_FULL);

  snprintf(full_path, sizeof(full_path), "%s/full", g_shm_base);
  assert(path_exists(full_path));
  assert(read_file_text(full_path, buf, sizeof(buf)) > 0);
  assert(strstr(buf, "@full") != NULL);

  stats_buffer_close(&sf);
  stats_buffer_collect_fixture_teardown(&fx);
}

static void test_legacy_writes_full(void)
{
  struct stats_buffer_collect_fixture fx;
  struct stats_buffer sf;
  char full_path[512];
  char buf[1024];
  const unsigned long long vals[2] = {11, 22};

  assert(stats_buffer_collect_fixture_init(&fx, "a,E b,E,R=S", vals, 2) == 0);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  memset(&sf, 0, sizeof(sf));
  assert(stats_buffer_open(&sf, "127.0.0.1", "5672", "q", "u", "p") == 0);
  free(sf.sf_data);
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;
  sf.sf_data_len = 0;
  assert(stats_buffer_collect(&sf) == 0);
  assert(stats_buffer_payload_row_tier(&sf) == STATS_ROW_LEGACY);
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_LEGACY);

  snprintf(full_path, sizeof(full_path), "%s/full", g_shm_base);
  assert(path_exists(full_path));
  assert(read_file_text(full_path, buf, sizeof(buf)) > 0);
  assert(strstr(buf, "@fast") == NULL);
  assert(strstr(buf, "@full") == NULL);
  assert(strstr(buf, "host_tt dev0") != NULL);

  stats_buffer_close(&sf);
  stats_buffer_collect_fixture_teardown(&fx);
}

static void test_second_write_overwrites(void)
{
  struct stats_buffer sf;
  char fast_path[512];
  char buf[1024];

  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("first-payload\n");
  assert(sf.sf_data != NULL);
  sf.sf_data_len = strlen(sf.sf_data);
  sf.sf_data_cap = sf.sf_data_len + 1;
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_FAST);

  free(sf.sf_data);
  sf.sf_data = strdup("second-payload\n");
  assert(sf.sf_data != NULL);
  sf.sf_data_len = strlen(sf.sf_data);
  sf.sf_data_cap = sf.sf_data_len + 1;
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_FAST);

  snprintf(fast_path, sizeof(fast_path), "%s/fast", g_shm_base);
  assert(read_file_text(fast_path, buf, sizeof(buf)) > 0);
  assert(strcmp(buf, "second-payload\n") == 0);

  free(sf.sf_data);
}

/* Regression: if the mirror dir is removed mid-run, recreate on next write. */
static void test_recreate_dir_after_rm(void)
{
  struct stats_buffer sf;
  char fast_path[512];
  char schema_path[512];
  char full_path[512];
  char buf[256];
  struct stat st;

  snprintf(fast_path, sizeof(fast_path), "%s/fast", g_shm_base);
  snprintf(schema_path, sizeof(schema_path), "%s/schema", g_shm_base);
  snprintf(full_path, sizeof(full_path), "%s/full", g_shm_base);
  unlink(fast_path);
  unlink(schema_path);
  unlink(full_path);
  assert(rmdir(g_shm_base) == 0);
  assert(stat(g_shm_base, &st) != 0);

  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("after-rmdir-payload\n");
  assert(sf.sf_data != NULL);
  sf.sf_data_len = strlen(sf.sf_data);
  sf.sf_data_cap = sf.sf_data_len + 1;
  stats_buffer_debug_shm_write_sample(&sf, STATS_ROW_FAST);

  assert(stat(g_shm_base, &st) == 0);
  assert(S_ISDIR(st.st_mode));
  assert(path_exists(fast_path));
  assert(read_file_text(fast_path, buf, sizeof(buf)) > 0);
  assert(strcmp(buf, "after-rmdir-payload\n") == 0);

  free(sf.sf_data);
}

#endif /* DEBUG */

static void test_daemon_write_hdr_gate(void)
{
  assert(stats_buffer_debug_shm_sample_wanted(0, 1) == 1);
  assert(stats_buffer_debug_shm_sample_wanted(1, 1) == 0);
  assert(stats_buffer_debug_shm_sample_wanted(0, 0) == 0);
  assert(stats_buffer_debug_shm_sample_wanted(1, 0) == 0);
  assert(stats_buffer_debug_shm_schema_wanted(1, 1) == 1);
  assert(stats_buffer_debug_shm_schema_wanted(0, 1) == 0);
}

#ifdef DEBUG

static void test_schema_writes_schema_file(void)
{
  struct stats_buffer sf;
  char schema_path[512];
  char buf[256];

  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("$hpcperfstats 3.0\n");
  assert(sf.sf_data != NULL);
  sf.sf_data_len = strlen(sf.sf_data);
  sf.sf_data_cap = sf.sf_data_len + 1;
  stats_buffer_debug_shm_write_payload(&sf, STATS_BUFFER_DEBUG_SHM_PAYLOAD_SCHEMA);

  snprintf(schema_path, sizeof(schema_path), "%s/schema", g_shm_base);
  assert(path_exists(schema_path));
  assert(read_file_text(schema_path, buf, sizeof(buf)) > 0);
  assert(strncmp(buf, "$hpcperfstats", 13) == 0);
  free(sf.sf_data);
}

#endif /* DEBUG */

int main(void)
{
#ifndef DEBUG
  test_daemon_write_hdr_gate();
  printf("test_stats_buffer_debug_shm: skipped (not a DEBUG build)\n");
  return 0;
#else
  test_daemon_write_hdr_gate();
  assert(setup_shm_dir() == 0);
  test_schema_writes_schema_file();
  test_second_write_overwrites();
  test_recreate_dir_after_rm();
  test_fast_writes_fast_only();
  test_full_writes_full_only();
  test_legacy_writes_full();
  printf("test_stats_buffer_debug_shm passed\n");
  return 0;
#endif
}
