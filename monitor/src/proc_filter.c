/* Linkable /proc pid filter shared by host_proc and job_proc_discover. */
#include <fnmatch.h>
#include <pwd.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "proc_filter.h"
#include "trace.h"

int proc_filter_is_denylisted_name(const char *pw_name)
{
  if (pw_name == NULL)
    return 0;
  return !strcmp("postfix", pw_name) || !strcmp("rpc", pw_name) || !strcmp("rpcuser", pw_name) ||
         !strcmp("dbus", pw_name) || !strcmp("daemon", pw_name) || !strcmp("ntp", pw_name);
}

int proc_filter_pid_at_root(const char *proc_root, const struct dirent *dir)
{
  char path[512];
  struct stat dirinfo;
  struct passwd *pwd;
  int n;

  if (proc_root == NULL || dir == NULL)
    return 0;
  if (fnmatch("[1-9]*", dir->d_name, 0))
    return 0;

  n = snprintf(path, sizeof(path), "%s/%s", proc_root, dir->d_name);
  if (n < 0 || (size_t)n >= sizeof(path))
    return 0;

  if (stat(path, &dirinfo) < 0 || dirinfo.st_uid == 0) {
    TRACE("Do not include this proc entry %s\n", path);
    return 0;
  }

  /* Unresolved uid (e.g. qemu foreign sysroot without host NSS): keep non-root. */
  pwd = getpwuid(dirinfo.st_uid);
  if (proc_filter_is_denylisted_name(pwd != NULL ? pwd->pw_name : NULL))
    return 0;

  return 1;
}
