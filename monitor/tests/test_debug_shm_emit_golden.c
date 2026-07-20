/*
 * Golden regression for @fast/@full sample payloads via debug shm write path.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "cpuid.h"
#include "collect_tier.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"
#include "stats_buffer_uts.h"
#include "test_debug_shm_emit_fixture.h"
#include "test_debug_shm_emit_validate.h"

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
  snprintf(g_shm_base, sizeof(g_shm_base), "%s/hpcperfstats_golden_XXXXXX", tmpdir);
  if (mkdtemp(g_shm_base) == NULL)
    return -1;
  if (setenv("HPCPERFSTATS_DEBUG_SHM_DIR", g_shm_base, 1) != 0)
    return -1;
  stats_buffer_debug_shm_init();
  return 0;
}

static const char *golden_srcdir(void)
{
  const char *s = getenv("srcdir");

  return (s != NULL && s[0] != '\0') ? s : ".";
}

static int golden_path(char *buf, size_t cap, const char *tier_file)
{
  return snprintf(buf, cap, "%s/expected/%s", golden_srcdir(), tier_file) >= (int)cap;
}

static int shm_tier_path(char *buf, size_t cap, const char *tier_file)
{
  return snprintf(buf, cap, "%s/%s", g_shm_base, tier_file) >= (int)cap;
}

static int read_file_text(const char *path, char **out, size_t *out_len)
{
  FILE *f;
  char *buf;
  size_t cap = 4096;
  size_t n = 0;
  size_t got;

  f = fopen(path, "r");
  if (f == NULL)
    return -1;
  buf = malloc(cap);
  if (buf == NULL) {
    fclose(f);
    return -1;
  }
  while ((got = fread(buf + n, 1, cap - n - 1, f)) > 0) {
    n += got;
    if (n + 1 >= cap) {
      char *nr = realloc(buf, cap * 2);

      if (nr == NULL) {
        free(buf);
        fclose(f);
        return -1;
      }
      buf = nr;
      cap *= 2;
    }
  }
  buf[n] = '\0';
  fclose(f);
  *out = buf;
  *out_len = n;
  return 0;
}

static int write_file_text(const char *path, const char *data, size_t len)
{
  FILE *f;

  f = fopen(path, "w");
  if (f == NULL)
    return -1;
  if (len > 0 && fwrite(data, 1, len, f) != len) {
    fclose(f);
    return -1;
  }
  if (fclose(f) != 0)
    return -1;
  return 0;
}

static void print_diff(const char *label, const char *expected, const char *actual)
{
  fprintf(stderr, "--- expected %s\n", label);
  fprintf(stderr, "+++ actual %s\n", label);
  fputs(expected, stderr);
  if (expected[0] != '\0' && expected[strlen(expected) - 1] != '\n')
    fputc('\n', stderr);
  fputs("---\n", stderr);
  fputs(actual, stderr);
  if (actual[0] != '\0' && actual[strlen(actual) - 1] != '\n')
    fputc('\n', stderr);
  fputs("+++\n", stderr);
}

static int compare_or_update_golden(const char *tier_file, const char *actual, size_t actual_len)
{
  char golden_file[512];
  char *expected = NULL;
  size_t expected_len = 0;
  const char *update = getenv("UPDATE_DEBUG_SHM_GOLDEN");

  if (golden_path(golden_file, sizeof(golden_file), tier_file) != 0)
    return -1;

  if (update != NULL && update[0] != '\0') {
    if (write_file_text(golden_file, actual, actual_len) != 0) {
      fprintf(stderr, "failed writing golden %s\n", golden_file);
      return -1;
    }
    fprintf(stderr, "updated golden %s\n", golden_file);
    return 0;
  }

  if (read_file_text(golden_file, &expected, &expected_len) != 0) {
    fprintf(stderr, "missing golden file %s (run UPDATE_DEBUG_SHM_GOLDEN=1)\n", golden_file);
    return -1;
  }

  if (expected_len != actual_len || memcmp(expected, actual, actual_len) != 0) {
    print_diff(tier_file, expected, actual);
    fprintf(stderr,
            "golden mismatch for %s\n"
            "Re-run: UPDATE_DEBUG_SHM_GOLDEN=1 make -C <builddir> check "
            "TESTS=test_debug_shm_emit_golden\n",
            tier_file);
    free(expected);
    return -1;
  }
  free(expected);
  return 0;
}

static const char *shm_tier_basename(enum stats_row_tier tier)
{
  if (tier == STATS_ROW_FAST)
    return "fast";
  return "full";
}

static int collect_and_check_tier(enum collect_phase phase, enum stats_row_tier tier,
                                  const char *golden_file)
{
  struct stats_buffer sf;
  char shm_file[512];
  char *payload = NULL;
  size_t payload_len = 0;
  int rc = -1;

  collect_tier_set_phase(phase);
  stats_buffer_uts_cache_reset();
  memset(&sf, 0, sizeof(sf));
  if (stats_buffer_open(&sf, "127.0.0.1", "5672", "q", "u", "p") != 0)
    return -1;
  free(sf.sf_data);
  sf.sf_data = strdup("");
  if (sf.sf_data == NULL)
    goto out;
  sf.sf_data_cap = 1;
  sf.sf_data_len = 0;
  if (stats_buffer_collect(&sf) != 0)
    goto out;

  stats_buffer_debug_shm_write_sample(&sf, tier);
  if (shm_tier_path(shm_file, sizeof(shm_file), shm_tier_basename(tier)) != 0)
    goto out;
  if (read_file_text(shm_file, &payload, &payload_len) != 0) {
    fprintf(stderr, "failed reading debug shm file %s\n", shm_file);
    goto out;
  }
  if (test_debug_shm_emit_validate_payload(payload, payload_len, tier) != 0) {
    fprintf(stderr, "driver shape validation failed for %s payload\n", shm_tier_basename(tier));
    goto out;
  }
  if (compare_or_update_golden(golden_file, payload, payload_len) != 0)
    goto out;
  rc = 0;

out:
  free(payload);
  stats_buffer_close(&sf);
  return rc;
}

#ifdef DEBUG

static int run_golden_checks(void)
{
  if (setup_shm_dir() != 0)
    return 1;
  if (test_debug_shm_emit_fixture_init() != 0)
    return 1;
  if (collect_and_check_tier(COLLECT_FAST_ONLY, STATS_ROW_FAST, "debug_shm_fast.txt") != 0)
    return 1;
  if (collect_and_check_tier(COLLECT_FULL, STATS_ROW_FULL, "debug_shm_full.txt") != 0)
    return 1;
  test_debug_shm_emit_fixture_teardown();
  return 0;
}

#endif /* DEBUG */

int main(void)
{
#ifndef DEBUG
  printf("test_debug_shm_emit_golden: skipped (not a DEBUG build)\n");
  return 0;
#else
  if (run_golden_checks() != 0)
    return 1;
  printf("test_debug_shm_emit_golden passed\n");
  return 0;
#endif
}
