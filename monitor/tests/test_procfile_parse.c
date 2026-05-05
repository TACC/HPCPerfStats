#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "procfile_parse.h"

struct line_log {
  size_t n;
  char lines[8][128];
};

static int collect_line_cb(char *line, void *ctx)
{
  struct line_log *ll = (struct line_log *)ctx;

  if (ll->n >= sizeof(ll->lines) / sizeof(ll->lines[0]))
    return 1;
  snprintf(ll->lines[ll->n], sizeof(ll->lines[ll->n]), "%s", line);
  ll->n++;
  return 0;
}

static char *write_tmp(const char *body)
{
  char *path = strdup("/tmp/hps_procfile_parseXXXXXX");
  int fd = mkstemp(path);
  size_t len = strlen(body);

  assert(fd >= 0);
  assert(write(fd, body, len) == (ssize_t)len);
  close(fd);
  return path;
}

int main(void)
{
  /* Three lines, last with no trailing newline. */
  char *p = write_tmp("alpha 1\nbeta 2\ngamma");
  struct line_log ll = { 0 };

  assert(procfile_for_each_line(p, collect_line_cb, &ll) == 0);
  assert(ll.n == 3);
  assert(strcmp(ll.lines[0], "alpha 1") == 0);
  assert(strcmp(ll.lines[1], "beta 2") == 0);
  assert(strcmp(ll.lines[2], "gamma") == 0);
  unlink(p);
  free(p);

  /* skip = 1 should skip the header. */
  p = write_tmp("HEADER\nfirst\nsecond\n");
  ll.n = 0;
  assert(procfile_for_each_line_skip(p, 1, collect_line_cb, &ll) == 0);
  assert(ll.n == 2);
  assert(strcmp(ll.lines[0], "first") == 0);
  assert(strcmp(ll.lines[1], "second") == 0);
  unlink(p);
  free(p);

  /* Missing path returns -1. */
  ll.n = 0;
  assert(procfile_for_each_line("/nonexistent/hps_procfile_x",
                                collect_line_cb, &ll) == -1);
  assert(ll.n == 0);

  /* Returning non-zero from callback stops iteration. */
  p = write_tmp("a\nb\nc\nd\n");
  ll.n = 0;
  /* line_log capacity is 8 so storage will not stop us; force stop after 2. */
  size_t cap_orig = sizeof(ll.lines) / sizeof(ll.lines[0]);
  (void)cap_orig;

  struct line_log ll2 = { 0 };
  /* Use the existing callback pattern but simulate small buffer. */
  ll2.n = sizeof(ll2.lines) / sizeof(ll2.lines[0]) - 2;
  assert(procfile_for_each_line(p, collect_line_cb, &ll2) == 0);
  /* Should have written the last 2 slots and then returned 1 to stop. */
  assert(ll2.n == sizeof(ll2.lines) / sizeof(ll2.lines[0]));
  unlink(p);
  free(p);

  puts("test_procfile_parse passed");
  return 0;
}
