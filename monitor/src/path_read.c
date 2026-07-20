/* Unified small-buffer and heap file reads for collect and pscanf paths. */
#include "path_read.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>

#include "path_open_fail_once.h"
#include "trace.h"

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

#define PATH_READ_ALLOC_INITIAL 8192u

static int path_read_open(const char *path, const struct path_read_opts *opts)
{
  int fd;

  if (path == NULL || opts == NULL) {
    errno = EINVAL;
    return -1;
  }

  if (opts->skip_known_bad && path_open_is_skipped(path)) {
    errno = ENOENT;
    return -1;
  }

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    int saved = errno;

    if (opts->skip_known_bad)
      path_open_record_failure_once(path);
    errno = saved;
    return -1;
  }

  return fd;
}

static int path_read_grow(char **buf, size_t *cap, const char *path,
                          const struct path_read_opts *opts)
{
  size_t ncap;
  char *nb;

  if (*cap >= PATH_READ_ALLOC_MAX) {
    if (opts->report_errors)
      ERROR("file `%s' exceeds PATH_READ_ALLOC_MAX\n", path);
    errno = EFBIG;
    return -1;
  }
  ncap = *cap * 2;
  if (ncap > PATH_READ_ALLOC_MAX)
    ncap = PATH_READ_ALLOC_MAX;
  nb = realloc(*buf, ncap);
  if (nb == NULL) {
    if (opts->report_errors)
      ERROR("cannot grow read buffer for `%s': %m\n", path);
    errno = ENOMEM;
    return -1;
  }
  *buf = nb;
  *cap = ncap;
  return 0;
}

int path_read_small(const char *path, char *buf, size_t bufsz, size_t *out_len,
                    const struct path_read_opts *opts)
{
  int fd;
  size_t total;

  if (path == NULL || buf == NULL || out_len == NULL || opts == NULL) {
    if (opts != NULL && opts->report_errors)
      ERROR("path_read_small: invalid arguments for `%s'\n", path != NULL ? path : "(null)");
    errno = EINVAL;
    return -1;
  }

  if (bufsz < 2) {
    if (opts->report_errors)
      ERROR("path_read_small: buffer too small for `%s'\n", path);
    errno = EINVAL;
    return -1;
  }

  fd = path_read_open(path, opts);
  if (fd < 0)
    return -1;

  total = 0;
  while (total + 1 < bufsz) {
    ssize_t n = read(fd, buf + total, bufsz - 1 - total);

    if (n < 0) {
      int saved = errno;

      if (opts->report_errors)
        ERROR("cannot read `%s': %m\n", path);
      close(fd);
      errno = saved;
      return -1;
    }
    if (n == 0)
      break;
    total += (size_t)n;
  }

  if (opts->detect_overflow && total == bufsz - 1) {
    char probe[1];
    ssize_t x = read(fd, probe, 1);

    if (x < 0) {
      int saved = errno;

      close(fd);
      errno = saved;
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

int path_read_alloc(const char *path, char **out_buf, size_t *out_len,
                    const struct path_read_opts *opts)
{
  int fd;
  size_t cap = PATH_READ_ALLOC_INITIAL;
  size_t len = 0;
  char *buf;

  if (path == NULL || out_buf == NULL || out_len == NULL || opts == NULL) {
    errno = EINVAL;
    return -1;
  }

  buf = malloc(cap);
  if (buf == NULL) {
    if (opts->report_errors)
      ERROR("cannot allocate read buffer for `%s': %m\n", path);
    errno = ENOMEM;
    return -1;
  }

  fd = path_read_open(path, opts);
  if (fd < 0) {
    free(buf);
    return -1;
  }

  for (;;) {
    if (len + 1 >= cap) {
      if (path_read_grow(&buf, &cap, path, opts) < 0) {
        free(buf);
        close(fd);
        return -1;
      }
    }

    {
      ssize_t n = read(fd, buf + len, cap - len - 1);
      if (n < 0) {
        int saved = errno;

        if (opts->report_errors)
          ERROR("cannot read `%s': %m\n", path);
        free(buf);
        close(fd);
        errno = saved;
        return -1;
      }
      if (n == 0)
        break;
      len += (size_t)n;
    }
  }

  close(fd);
  buf[len] = '\0';
  *out_buf = buf;
  *out_len = len;
  return 0;
}
