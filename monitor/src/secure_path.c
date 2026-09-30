/* Private directory create/validate for daemon dump and DEBUG shm paths. */
#include "secure_path.h"

#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>

int ensure_private_dir(const char *path, mode_t mode)
{
  struct stat st;

  if (path == NULL || path[0] == '\0')
    return -1;

  if (mkdir(path, mode) < 0 && errno != EEXIST)
    return -1;

  if (lstat(path, &st) < 0)
    return -1;
  if (!S_ISDIR(st.st_mode))
    return -1;
  if (st.st_uid != geteuid())
    return -1;
  if ((st.st_mode & 022) != 0) {
    if (chmod(path, mode) < 0)
      return -1;
    if (lstat(path, &st) < 0)
      return -1;
    if ((st.st_mode & 022) != 0)
      return -1;
  }
  return 0;
}
