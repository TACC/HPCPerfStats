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
/* Match collect.c COLLECT_SMALL_BUF: stack read avoids malloc on tiny JOBID/sysfs files. */
#define PSCANF_SMALL_BUF 4096

/* Read into stack buffer; NUL-terminate. Returns 0 on success, -1 on I/O error, 1 if file exceeds buffer (use slurp). */
static int path_read_small(const char *path, char *buf, size_t bufsz, size_t *out_len)
{
  int fd;
  size_t total;

  if (bufsz < 2)
    return -1;

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0)
    return -1;

  total = 0;
  while (total + 1 < bufsz) {
    ssize_t n = read(fd, buf + total, bufsz - 1 - total);

    if (n < 0) {
      int saverr = errno;

      close(fd);
      errno = saverr;
      return -1;
    }
    if (n == 0)
      break;
    total += (size_t)n;
  }

  if (total == bufsz - 1) {
    char probe[1];
    ssize_t x = read(fd, probe, 1);

    if (x < 0) {
      int saverr = errno;

      close(fd);
      errno = saverr;
      return -1;
    }
    if (x > 0) {
      close(fd);
      return 1;
    }
  }

  close(fd);
  buf[total] = '\0';
  *out_len = total;
  return 0;
}

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
  char sbuf[PSCANF_SMALL_BUF];
  char *buf = sbuf;
  size_t len;
  va_list ap;
  int n;
  int small_rc;

  small_rc = path_read_small(path, sbuf, sizeof(sbuf), &len);
  if (small_rc < 0)
    return -1;
  if (small_rc != 0) {
    if (path_slurp(path, &buf, &len) < 0)
      return -1;
  }

  va_start(ap, fmt);
  n = vsscanf(buf, fmt, ap);
  va_end(ap);
  if (buf != sbuf)
    free(buf);
  return n;
}
