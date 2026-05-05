#include "sys_iter.h"

#include <dirent.h>
#include <stddef.h>

#include "path_open_fail_once.h"

int sys_iter_for_each(const char *base, sys_iter_cb_fn cb, void *ctx)
{
  DIR *dir = path_opendir_or_record_fail(base);
  struct dirent *ent;

  if (dir == NULL)
    return -1;

  while ((ent = readdir(dir)) != NULL) {
    if (ent->d_name[0] == '.')
      continue;
    if (cb != NULL)
      cb(base, ent->d_name, ctx);
  }

  closedir(dir);
  return 0;
}
