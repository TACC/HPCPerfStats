#include <assert.h>
#include <errno.h>
#include <string.h>

#include "path_open_fail_once.h"

int main(void)
{
  static const char p[] = "/proc/this_path_should_not_exist_hpcperfstats_test";

  assert(path_open_is_skipped(p) == 0);

  errno = ENOENT;
  path_open_record_failure_once(p);
  assert(path_open_is_skipped(p) != 0);

  errno = ENOENT;
  path_open_record_failure_once(p);

  path_fail_mark("silent-mark-key");
  assert(path_open_is_skipped("silent-mark-key") != 0);

  return 0;
}
