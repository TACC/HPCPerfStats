/* BeeGFS mount-entry helpers (unit-tested). */
#include "beegfs_mount.h"

#include <ctype.h>
#include <string.h>

int beegfs_fstype_is_beegfs(const char *fstype)
{
  if (fstype == NULL)
    return 0;
  return (strcmp(fstype, "beegfs") == 0 || strcmp(fstype, "beegfs_nodev") == 0) ? 1 : 0;
}

int beegfs_cfgfile_from_mnt_opts(const char *opts, char *out, size_t out_sz)
{
  static const char key[] = "cfgFile=";
  const char *p;
  size_t n;

  if (opts == NULL || out == NULL || out_sz == 0)
    return -1;

  p = strstr(opts, key);
  if (p == NULL)
    return -1;
  p += sizeof(key) - 1;
  n = 0;
  while (p[n] != '\0' && p[n] != ',' && !isspace((unsigned char)p[n]))
    n++;
  if (n == 0 || n >= out_sz)
    return -1;
  memcpy(out, p, n);
  out[n] = '\0';
  return 0;
}
