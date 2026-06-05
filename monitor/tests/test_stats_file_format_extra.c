/* Extended stats_file_format coverage: NULL guards and temp-file header lines. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "schema.h"
#include "stats_file_format.h"

static char *write_tmp_line(const char *body)
{
  char tmpl[] = "/tmp/hps_sff_extraXXXXXX";
  int fd = mkstemp(tmpl);
  size_t len = strlen(body);

  assert(fd >= 0);
  assert(write(fd, body, len) == (ssize_t) len);
  close(fd);
  return strdup(tmpl);
}

static void read_first_line(const char *path, char *buf, size_t bufsz)
{
  FILE *f = fopen(path, "r");

  assert(f != NULL);
  assert(fgets(buf, (int) bufsz, f) != NULL);
  fclose(f);
}

static void test_validate_null_args(void)
{
  char line[128];

  snprintf(line, sizeof(line), "$%s %s\n", STATS_PROGRAM, STATS_VERSION);
  assert(stats_file_validate_program_header(NULL, line) < 0);
  assert(stats_file_validate_program_header("/tmp/x", NULL) < 0);
}

static void test_validate_from_temp_file(void)
{
  char *path;
  char line[256];
  char buf[256];

  snprintf(line, sizeof(line), "$%s %s\n", STATS_PROGRAM, STATS_VERSION);
  path = write_tmp_line(line);
  read_first_line(path, buf, sizeof(buf));
  assert(stats_file_validate_program_header(path, buf) == 0);
  unlink(path);
  free(path);

  snprintf(line, sizeof(line), "# comment only\n");
  path = write_tmp_line(line);
  read_first_line(path, buf, sizeof(buf));
  assert(stats_file_validate_program_header(path, buf) < 0);
  unlink(path);
  free(path);
}

static void test_suffix_and_mark_null_guards(void)
{
  stats_file_fprint_schema_entry_suffix(NULL, NULL);
  stats_file_fprint_mark_multiline(NULL, '%', "noop");
}

int main(void)
{
  test_validate_null_args();
  test_validate_from_temp_file();
  test_suffix_and_mark_null_guards();
  printf("test_stats_file_format_extra passed\n");
  return 0;
}
