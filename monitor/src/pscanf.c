#include "pscanf.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

#define PSCANF_READ_MAX (1u << 20)

static int path_slurp(const char *path, char **out_buf, size_t *out_len)
{
  int fd = -1;
  size_t cap = 8192;
  size_t len = 0;
  char *buf = malloc(cap);

  if (buf == NULL) {
    errno = ENOMEM;
    return -1;
  }

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    free(buf);
    return -1;
  }

  for (;;) {
    if (len + 1 >= cap) {
      if (cap >= PSCANF_READ_MAX) {
	free(buf);
	close(fd);
	errno = EFBIG;
	return -1;
      }
      size_t ncap = cap * 2;

      if (ncap > PSCANF_READ_MAX)
	ncap = PSCANF_READ_MAX;
      char *nb = realloc(buf, ncap);

      if (nb == NULL) {
	free(buf);
	close(fd);
	errno = ENOMEM;
	return -1;
      }
      buf = nb;
      cap = ncap;
    }
    ssize_t n = read(fd, buf + len, cap - len - 1);

    if (n < 0) {
      int saverr = errno;

      free(buf);
      close(fd);
      errno = saverr;
      return -1;
    }
    if (n == 0)
      break;
    len += (size_t)n;
  }
  close(fd);
  buf[len] = '\0';
  *out_buf = buf;
  *out_len = len;
  return 0;
}

int pscanf(const char *path, const char *fmt, ...)
{
  char *buf = NULL;
  size_t len;
  va_list ap;
  int n;

  if (path_slurp(path, &buf, &len) < 0)
    return -1;

  va_start(ap, fmt);
  n = vsscanf(buf, fmt, ap);
  va_end(ap);
  free(buf);
  return n;
}
