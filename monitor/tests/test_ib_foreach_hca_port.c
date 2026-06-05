/* ib_foreach_hca_port walks /sys/class/infiniband (hardcoded). Fake sysfs under
 * TMPDIR cannot be injected without production changes; this driver covers the
 * NULL-callback guard and documents the sysfs constraint. */
#include <assert.h>
#include <stdio.h>

#include "ib_common.h"

static void test_null_fn_returns_early(void)
{
  ib_foreach_hca_port(NULL, NULL);
}

int main(void)
{
  test_null_fn_returns_early();
  printf("test_ib_foreach_hca_port passed (NULL fn guard; sysfs path hardcoded)\n");
  return 0;
}
