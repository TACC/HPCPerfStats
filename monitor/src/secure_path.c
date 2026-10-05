/* Private directory create/validate for daemon dump and DEBUG shm paths. */
#include "secure_path.h"

#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

static int verify_private_dir_fd(int fd, mode_t mode)
{
  struct stat st;

  if (fstat(fd, &st) < 0)
    return -1;
  if (!S_ISDIR(st.st_mode))
    return -1;
  if (st.st_uid != geteuid())
    return -1;
  if ((st.st_mode & 022) != 0) {
    if (fchmod(fd, mode) < 0)
      return -1;
    if (fstat(fd, &st) < 0)
      return -1;
    if ((st.st_mode & 022) != 0)
      return -1;
  }
  return 0;
}

int ensure_private_dir(const char *path, mode_t mode)
{
  int fd;
  int rc;

  if (path == NULL || path[0] == '\0')
    return -1;

  if (mkdir(path, mode) < 0 && errno != EEXIST)
    return -1;

  fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0)
    return -1;
  rc = verify_private_dir_fd(fd, mode);
  close(fd);
  return rc;
}
