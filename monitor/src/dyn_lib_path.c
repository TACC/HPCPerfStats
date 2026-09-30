/* Allowlist absolute paths for privileged dlopen overrides. */
#include "dyn_lib_path.h"

#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int dyn_lib_path_allowed(const char *path)
{
  static const char *const prefixes[] = {
      "/usr/lib64/",
      "/usr/lib/",
      NULL,
  };
  struct stat st;
  size_t i;

  if (path == NULL || path[0] != '/')
    return 0;
  if (strstr(path, "/../") != NULL)
    return 0;
  if (strlen(path) >= 3) {
    size_t n = strlen(path);
    if (n >= 3 && strcmp(path + n - 3, "/..") == 0)
      return 0;
  }
  for (i = 0; prefixes[i] != NULL; i++) {
    size_t plen = strlen(prefixes[i]);
    if (strncmp(path, prefixes[i], plen) == 0)
      break;
  }
  if (prefixes[i] == NULL)
    return 0;
  if (lstat(path, &st) < 0)
    return 0;
  if (!S_ISREG(st.st_mode))
    return 0;
  return 1;
}
