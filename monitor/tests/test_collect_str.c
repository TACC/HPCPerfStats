#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "collect.h"
#include "path_read.h"
#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static const struct path_read_opts collect_read_opts = {
    .skip_known_bad = 1,
    .report_errors = 0,
    .detect_overflow = 0,
};

static char *write_temp_file(const char *content)
{
  char tmpl[] = "/tmp/hps_collect_strXXXXXX";
  int fd = mkstemp(tmpl);

  assert(fd >= 0);
  assert(write(fd, content, strlen(content)) == (ssize_t)strlen(content));
  close(fd);
  return strdup(tmpl);
}

static void strip_trailing_newline(char *buf)
{
  size_t len = strlen(buf);

  while (len > 0 && (buf[len - 1] == '\n' || buf[len - 1] == '\r')) {
    buf[len - 1] = '\0';
    len--;
  }
}

static void read_temp_into(char *buf, size_t bufsz, const char *path)
{
  size_t len = 0;

  assert(path_read_small(path, buf, bufsz, &len, &collect_read_opts) == 0);
  strip_trailing_newline(buf);
}

static void test_str_collect_key_list(void)
{
  struct test_stats_stub stub;
  char *path;
  char content[64];
  unsigned long long val;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  path = write_temp_file("10 20 30\n");
  read_temp_into(content, sizeof(content), path);
  assert(str_collect_key_list(content, &g_dummy_stats, "a", "b", "c", NULL) == 3);
  assert(test_stats_stub_find(&stub, "a", &val) && val == 10ULL);
  assert(test_stats_stub_find(&stub, "b", &val) && val == 20ULL);
  assert(test_stats_stub_find(&stub, "c", &val) && val == 30ULL);

  assert(str_collect_key_list(NULL, &g_dummy_stats, "a", NULL) == -1);
  assert(str_collect_key_list("10", NULL, "a", NULL) == -1);

  unlink(path);
  free(path);
  test_stats_stub_unbind();
}

static void test_str_collect_prefix_key_list(void)
{
  struct test_stats_stub stub;
  char *path;
  char content[64];
  unsigned long long val;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  path = write_temp_file("1 2\n");
  read_temp_into(content, sizeof(content), path);
  assert(str_collect_prefix_key_list(content, &g_dummy_stats, "pfx_", "a", "b", NULL) == 2);
  assert(test_stats_stub_find(&stub, "pfx_a", &val) && val == 1ULL);
  assert(test_stats_stub_find(&stub, "pfx_b", &val) && val == 2ULL);

  assert(str_collect_prefix_key_list(NULL, &g_dummy_stats, "pfx_", "a", NULL) == -1);
  assert(str_collect_prefix_key_list("1", &g_dummy_stats, NULL, "a", NULL) == -1);

  unlink(path);
  free(path);
  test_stats_stub_unbind();
}

int main(void)
{
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  test_str_collect_key_list();
  test_str_collect_prefix_key_list();

  test_stats_stub_unbind();
  printf("test_collect_str passed\n");
  return 0;
}
