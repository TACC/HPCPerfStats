#include "intel_mmconfig.h"

#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <sys/mman.h>
#include <unistd.h>

#include "path_open_fail_once.h"
#include "trace.h"

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

static const char intel_mmconfig_path[] = "/dev/mem";

int intel_mmconfig_open(struct intel_mmconfig *out, uint64_t base, uint64_t size)
{
  int fd = -1;
  uint32_t *map = MAP_FAILED;
  int saved;

  if (out == NULL) {
    errno = EINVAL;
    return -1;
  }

  out->fd = -1;
  out->map = MAP_FAILED;
  out->base = base;
  out->size = size;

  if (path_open_is_skipped(intel_mmconfig_path)) {
    errno = ENOENT;
    return -1;
  }

  fd = open(intel_mmconfig_path, O_RDWR | O_CLOEXEC);
  if (fd < 0) {
    saved = errno;
    path_open_record_failure_once(intel_mmconfig_path);
    errno = saved;
    return -1;
  }

  map = (uint32_t *)mmap(NULL, (size_t)size,
                         PROT_READ | PROT_WRITE, MAP_SHARED,
                         fd, (off_t)base);
  if (map == MAP_FAILED) {
    saved = errno;
    ERROR("cannot mmap `%s': %m\n", intel_mmconfig_path);
    close(fd);
    errno = saved;
    return -1;
  }

  out->fd = fd;
  out->map = map;
  return 0;
}

void intel_mmconfig_close(struct intel_mmconfig *m)
{
  if (m == NULL)
    return;
  if (m->map != MAP_FAILED) {
    munmap(m->map, (size_t)m->size);
    m->map = MAP_FAILED;
  }
  if (m->fd >= 0) {
    close(m->fd);
    m->fd = -1;
  }
}
