#include "msr_io.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

#include "path_open_fail_once.h"

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

int msr_open_cpu(const char *cpu, int flags)
{
  char path[80];
  int fd;
  int saved;

  if (cpu == NULL) {
    errno = EINVAL;
    return -1;
  }

  if (snprintf(path, sizeof(path), "/dev/cpu/%s/msr", cpu) >= (int)sizeof(path)) {
    errno = ENAMETOOLONG;
    return -1;
  }

  if (path_open_is_skipped(path)) {
    errno = ENOENT;
    return -1;
  }

  fd = open(path, flags | O_CLOEXEC);
  if (fd < 0) {
    saved = errno;
    path_open_record_failure_once(path);
    errno = saved;
    return -1;
  }
  return fd;
}

int msr_read_u64(int fd, unsigned int offset, uint64_t *val)
{
  ssize_t n;

  if (val == NULL) {
    errno = EINVAL;
    return -1;
  }
  n = pread(fd, val, sizeof(*val), (off_t)offset);

  if (n < 0)
    return -1;
  if (n != sizeof(*val)) {
    errno = EIO;
    return -1;
  }
  return 0;
}

int msr_write_u64(int fd, unsigned int offset, uint64_t val)
{
  ssize_t n = pwrite(fd, &val, sizeof(val), (off_t)offset);

  if (n < 0)
    return -1;
  if (n != sizeof(val)) {
    errno = EIO;
    return -1;
  }
  return 0;
}
