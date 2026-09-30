#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "dyn_lib_path.h"

int main(void)
{
  assert(dyn_lib_path_allowed(NULL) == 0);
  assert(dyn_lib_path_allowed("") == 0);
  assert(dyn_lib_path_allowed("libdcgm.so") == 0);
  assert(dyn_lib_path_allowed("/tmp/evil.so") == 0);
  assert(dyn_lib_path_allowed("/usr/lib64/../tmp/evil.so") == 0);
  assert(dyn_lib_path_allowed("/home/user/lib.so") == 0);
  /* May or may not exist on this host; if present must be allowed. */
  if (dyn_lib_path_allowed("/usr/lib64/libc.so.6") || dyn_lib_path_allowed("/usr/lib/libc.so.6"))
    assert(1);
  printf("test_dyn_lib_path passed\n");
  return 0;
}
