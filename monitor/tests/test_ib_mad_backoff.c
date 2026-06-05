/* Fresh MAD backoff state: cycle_ok helpers allow collection. */
#include <assert.h>
#include <stdio.h>

#include "ib_mad.h"

int main(void)
{
  assert(ib_mad_ext_collect_cycle_ok() == 1);
  assert(ib_mad_sw_collect_cycle_ok() == 1);
  printf("test_ib_mad_backoff passed\n");
  return 0;
}
