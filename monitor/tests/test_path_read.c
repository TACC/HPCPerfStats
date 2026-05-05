#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "path_read.h"

static const struct path_read_opts collect_opts = {
  .skip_known_bad  = 1,
  .report_errors   = 0,
  .detect_overflow = 0,
};

static const struct path_read_opts pscanf_opts = {
  .skip_known_bad  = 0,
  .report_errors   = 0,
  .detect_overflow = 1,
};

static char *make_tmp(const char *body, size_t len)
{
  char *path = strdup("/tmp/hps_path_readXXXXXX");
  int fd = mkstemp(path);

  assert(fd >= 0);
  assert(write(fd, body, len) == (ssize_t)len);
  close(fd);
  return path;
}

int main(void)
{
  /* Small read into stack buffer with collect-style options. */
  const char *body = "hello world\n";
  char *p = make_tmp(body, strlen(body));
  char buf[128];
  size_t len = 0;

  assert(path_read_small(p, buf, sizeof(buf), &len, &collect_opts) == 0);
  assert(len == strlen(body));
  assert(memcmp(buf, body, len) == 0);
  assert(buf[len] == '\0');
  unlink(p);
  free(p);

  /* Overflow detection in pscanf-style mode. */
  size_t big = 6000;
  char *bigbody = malloc(big);
  memset(bigbody, 'a', big);
  bigbody[big - 1] = '\n';
  p = make_tmp(bigbody, big);
  char small[128];

  int rc = path_read_small(p, small, sizeof(small), &len, &pscanf_opts);
  assert(rc == 1);

  /* Allocating reader returns the full content. */
  char *all = NULL;
  size_t all_len = 0;

  assert(path_read_alloc(p, &all, &all_len, &pscanf_opts) == 0);
  assert(all_len == big);
  assert(memcmp(all, bigbody, big) == 0);
  free(all);
  unlink(p);
  free(p);
  free(bigbody);

  /* Missing file: returns -1, errno preserved (silent). */
  errno = 0;
  rc = path_read_small("/nonexistent/hps_path_read_xx", buf, sizeof(buf), &len, &pscanf_opts);
  assert(rc == -1);
  assert(errno == ENOENT);

  /* Buffer too small returns -1 with EINVAL. */
  errno = 0;
  rc = path_read_small("/dev/null", buf, 1, &len, &pscanf_opts);
  assert(rc == -1);
  assert(errno == EINVAL);

  /* PATH_READ_ALLOC_MAX cap: write a file just under and well over the cap. */
  size_t under = 4096;
  char *u = malloc(under);
  memset(u, 'x', under);
  p = make_tmp(u, under);
  all = NULL;
  all_len = 0;
  assert(path_read_alloc(p, &all, &all_len, &pscanf_opts) == 0);
  assert(all_len == under);
  free(all);
  unlink(p);
  free(p);
  free(u);

  puts("test_path_read passed");
  return 0;
}
