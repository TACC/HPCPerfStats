/* ib_port_collectible sysfs integration is hardcoded to /sys/class/infiniband.
 * Full fake-sysfs coverage is not attempted here; parser logic lives in
 * test_ib_port_state.c (including link_layer InfiniBand vs Ethernet).
 * Collectible composition: ACTIVE or phys LinkUp, then if link_layer exists
 * require InfiniBand (missing file remains allowed). This driver exercises
 * safe early-return paths only. */
#include <assert.h>
#include <stdio.h>

#include "ib_common.h"

static void test_null_hca_returns_not_collectible(void)
{
  assert(ib_port_collectible(NULL, 1) == 0);
  assert(ib_port_collectible(NULL, 0) == 0);
}

int main(void)
{
  test_null_hca_returns_not_collectible();
  printf("test_ib_port_collectible passed (NULL hca only; sysfs path hardcoded)\n");
  return 0;
}
