/* collect.c helpers set value sentinels instead of skipping keys or storing 0 on failure:
 * str_collect_key_list / str_collect_prefix_key_list / path_collect_key_list parse failures and
 * path_collect_key_value(_dir) misses, plus path_collect_single return codes. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "collect.h"
#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
static struct stats_type g_type;
static char g_tmpdir[] = "/tmp/hps_collect_sentXXXXXX";

enum { K_A, K_B, K_C, K_D };

static struct stats *fresh_stats(const char *dev)
{
  struct stats *s = get_current_stats(&g_type, dev);

  assert(s != NULL);
  stats_type_mark_not_updated(&g_type);
  return s;
}

static char *tmp_path(const char *name)
{
  static char path[256];

  snprintf(path, sizeof(path), "%s/%s", g_tmpdir, name);
  return path;
}

static const char *write_tmp(const char *name, const char *content)
{
  const char *path = tmp_path(name);
  FILE *f = fopen(path, "w");

  assert(f != NULL);
  fputs(content, f);
  fclose(f);
  return path;
}

static void test_str_collect_key_list_bad_token(void)
{
  struct stats *s = fresh_stats("str");

  assert(str_collect_key_list("1 2 x 4", s, "a", "b", "c", "d", NULL) == 2);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_OK && s->s_val[K_A] == 1ULL);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_OK && s->s_val[K_B] == 2ULL);
  assert(s->s_val_state[K_C] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[K_D] == STATS_SENTINEL_READ_FAILED);
}

/* strtoull with no digits must not store 0: the prior value stays behind READ_FAILED. */
static void test_str_collect_key_list_no_digits_keeps_value(void)
{
  struct stats *s = fresh_stats("nodigits");

  stats_set(s, "b", 99ULL);
  stats_type_mark_not_updated(&g_type);
  assert(str_collect_key_list("5 -", s, "a", "b", NULL) == 1);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val[K_B] == 99ULL);

  assert(str_collect_key_list("", s, "a", NULL) == 0);
}

static void test_str_collect_prefix_key_list_short_line(void)
{
  struct stats *s = fresh_stats("prefix");

  assert(str_collect_prefix_key_list("8", s, "", "a", "b", NULL) == 1);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_OK && s->s_val[K_A] == 8ULL);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_READ_FAILED);
}

static void test_path_collect_key_list(void)
{
  struct stats *s = fresh_stats("list");
  const char *path = write_tmp("list", "10 20\n");

  assert(path_collect_key_list(path, s, "a", "b", "c", NULL) == 2);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_OK && s->s_val[K_B] == 20ULL);
  assert(s->s_val_state[K_C] == STATS_SENTINEL_READ_FAILED);

  s = fresh_stats("list");
  assert(path_collect_key_list(tmp_path("absent_list"), s, "a", "b", NULL) == -1);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++)
    assert(s->s_val_state[k] == STATS_SENTINEL_NOT_PRESENT);
}

static void test_path_collect_key_value(void)
{
  struct stats *s = fresh_stats("kv");
  const char *path = write_tmp("kv", "a 5\nb junk\nzz 3\n");

  assert(path_collect_key_value(path, s) == 0);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_OK && s->s_val[K_A] == 5ULL);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_READ_FAILED && s->s_val[K_B] == 0ULL);
  assert(s->s_val_state[K_C] == STATS_SENTINEL_NOT_PRESENT);
  assert(s->s_val_state[K_D] == STATS_SENTINEL_NOT_PRESENT);

  s = fresh_stats("kv");
  assert(path_collect_key_value(tmp_path("absent_kv"), s) == -1);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_NOT_PRESENT);
}

static void test_path_collect_key_value_dir(void)
{
  struct stats *s = fresh_stats("dir");
  char dir[256];

  snprintf(dir, sizeof(dir), "%s", tmp_path("counters"));
  assert(mkdir(dir, 0700) == 0);
  write_tmp("counters/a", "7\n");
  write_tmp("counters/b", "junk\n");

  assert(path_collect_key_value_dir(dir, s) == 0);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_OK && s->s_val[K_A] == 7ULL);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[K_C] == STATS_SENTINEL_NOT_PRESENT);

  /* A second directory may still supply keys the first one lacked. */
  snprintf(dir, sizeof(dir), "%s", tmp_path("hw_counters"));
  assert(mkdir(dir, 0700) == 0);
  write_tmp("hw_counters/c", "9\n");
  assert(path_collect_key_value_dir(dir, s) == 0);
  assert(s->s_val_state[K_C] == STATS_SENTINEL_OK && s->s_val[K_C] == 9ULL);
  assert(s->s_val_state[K_B] == STATS_SENTINEL_READ_FAILED);

  s = fresh_stats("dir");
  assert(path_collect_key_value_dir(tmp_path("absent_dir"), s) == -1);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_NOT_PRESENT);
  /* The second tick skips the known-bad path and still reports NOT_PRESENT. */
  s = fresh_stats("dir");
  assert(path_collect_key_value_dir(tmp_path("absent_dir"), s) == -1);
  assert(s->s_val_state[K_D] == STATS_SENTINEL_NOT_PRESENT);
}

static void test_path_collect_key_list_permission_denied(void)
{
  struct stats *s;
  const char *path;

  if (geteuid() == 0)
    return; /* root reads mode 000 files */
  path = write_tmp("denied", "1 2\n");
  assert(chmod(path, 0) == 0);
  s = fresh_stats("denied");
  assert(path_collect_key_list(path, s, "a", NULL) == -1);
  assert(s->s_val_state[K_A] == STATS_SENTINEL_PERMISSION_DENIED);
}

static void test_path_collect_single_return_codes(void)
{
  unsigned long long v = 42ULL;

  assert(path_collect_single(write_tmp("single", "123\n"), &v) == 1 && v == 123ULL);
  v = 42ULL;
  assert(path_collect_single(write_tmp("single_bad", "x\n"), &v) == 0 && v == 42ULL);
  assert(path_collect_single(tmp_path("absent_single"), &v) == -1 && v == 42ULL);
  assert(path_collect_single(NULL, &v) == -1);
}

static void test_errno_mapping(void)
{
  assert(stats_sentinel_for_errno(ENOENT) == STATS_SENTINEL_NOT_PRESENT);
  assert(stats_sentinel_for_errno(ENODEV) == STATS_SENTINEL_NOT_PRESENT);
  assert(stats_sentinel_for_errno(EACCES) == STATS_SENTINEL_PERMISSION_DENIED);
  assert(stats_sentinel_for_errno(EPERM) == STATS_SENTINEL_PERMISSION_DENIED);
  assert(stats_sentinel_for_errno(EBUSY) == STATS_SENTINEL_PMU_BUSY);
  assert(stats_sentinel_for_errno(EIO) == STATS_SENTINEL_READ_FAILED);
  assert(stats_sentinel_for_errno(0) == STATS_SENTINEL_READ_FAILED);
}

int main(void)
{
  char cmd[512];

  assert(mkdtemp(g_tmpdir) != NULL);
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_tt");
  g_type.st_schema_def = "a,E b,E c,E d,E";
  assert(stats_type_init(&g_type) == 0);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  test_str_collect_key_list_bad_token();
  test_str_collect_key_list_no_digits_keeps_value();
  test_str_collect_prefix_key_list_short_line();
  test_path_collect_key_list();
  test_path_collect_key_value();
  test_path_collect_key_value_dir();
  test_path_collect_key_list_permission_denied();
  test_path_collect_single_return_codes();
  test_errno_mapping();

  stats_type_destroy(&g_type);
  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", g_tmpdir);
  assert(system(cmd) == 0);
  printf("test_collect_sentinels passed\n");
  return 0;
}
