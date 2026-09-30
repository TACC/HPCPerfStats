#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "secure_path.h"

int main(void)
{
  char tmpl[] = "/tmp/hpcperfstats-secure-path-XXXXXX";
  char *dir = mkdtemp(tmpl);
  char hijack[256];
  struct stat st;

  assert(dir != NULL);
  assert(ensure_private_dir(dir, 0700) == 0);
  assert(lstat(dir, &st) == 0);
  assert(S_ISDIR(st.st_mode));
  assert((st.st_mode & 022) == 0);

  snprintf(hijack, sizeof(hijack), "%s/link", dir);
  if (symlink("/tmp", hijack) == 0)
    assert(ensure_private_dir(hijack, 0700) < 0);

  assert(ensure_private_dir(NULL, 0700) < 0);
  assert(ensure_private_dir("", 0700) < 0);

  (void)rmdir(dir);
  printf("test_secure_path passed\n");
  return 0;
}
