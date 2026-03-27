#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats_file_format.h"

static struct schema_entry *make_entry(const char *key, unsigned int se_type,
				       const char *unit, unsigned int width)
{
  size_t klen = strlen(key);
  struct schema_entry *se =
      (struct schema_entry *)malloc(sizeof(*se) + klen + 1);
  assert(se != NULL);
  memset(se, 0, sizeof(*se));
  strcpy(se->se_key, key);
  se->se_type = se_type;
  se->se_width = width;
  if (unit != NULL) {
    se->se_unit = strdup(unit);
    assert(se->se_unit != NULL);
  }
  return se;
}

static void test_validate_good_header(void)
{
  char buf[256];
  snprintf(buf, sizeof(buf), "$%s %s\n", STATS_PROGRAM, STATS_VERSION);
  assert(stats_file_validate_program_header("/fake/path", buf) == 0);
}

static void test_validate_rejects_bad_magic(void)
{
  char buf[256];
  snprintf(buf, sizeof(buf), "!%s %s\n", STATS_PROGRAM, STATS_VERSION);
  assert(stats_file_validate_program_header("/fake/path", buf) < 0);
}

static void test_validate_rejects_bad_program(void)
{
  char buf[256];
  snprintf(buf, sizeof(buf), "$not%s %s\n", STATS_PROGRAM, STATS_VERSION);
  assert(stats_file_validate_program_header("/fake/path", buf) < 0);
}

static void test_validate_rejects_version_too_new(void)
{
  char buf[256];
  snprintf(buf, sizeof(buf), "$%s 999.999\n", STATS_PROGRAM);
  assert(stats_file_validate_program_header("/fake/path", buf) < 0);
}

static void test_suffix_control_event_unit_width(void)
{
  struct schema_entry *se =
      make_entry("k", SE_EVENT, "ms", 4);
  char *out = NULL;
  size_t n = 0;
  FILE *f = open_memstream(&out, &n);
  assert(f != NULL);
  stats_file_fprint_schema_entry_suffix(f, se);
  fclose(f);

  assert(strstr(out, ",E") != NULL);
  assert(strstr(out, ",U=ms") != NULL);
  assert(strstr(out, ",W=4") != NULL);
  free(out);
  free(se->se_unit);
  free(se);

  se = make_entry("c", SE_CONTROL, NULL, 0);
  f = open_memstream(&out, &n);
  assert(f != NULL);
  stats_file_fprint_schema_entry_suffix(f, se);
  fclose(f);
  assert(strstr(out, ",C") != NULL);
  assert(strstr(out, ",E") == NULL);
  free(out);
  free(se);
}

int main(void)
{
  test_validate_good_header();
  test_validate_rejects_bad_magic();
  test_validate_rejects_bad_program();
  test_validate_rejects_version_too_new();
  test_suffix_control_event_unit_width();
  printf("test_stats_file_format passed\n");
  return 0;
}
