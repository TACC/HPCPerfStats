/* hwdetect probe-cache invalidation and optional-stack probe smoke tests. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "hwdetect.h"
#include "stats.h"

struct stats_type *stats_type_get(const char *name)
{
  (void)name;
  return NULL;
}

void ib_family_disable_all(void) {}

static void test_invalidate_probe_cache_idempotent(void)
{
  int nvidia1 = -1;
  int amd1 = -1;
  int intel1 = -1;
  int ib1 = -1;
  int opa1 = -1;
  int nvidia2 = -1;
  int amd2 = -1;
  int intel2 = -1;
  int ib2 = -1;
  int opa2 = -1;

  hwdetect_probe_optional_stack_presence(&nvidia1, &amd1, &intel1, &ib1, &opa1);
  hwdetect_invalidate_probe_cache();
  hwdetect_probe_optional_stack_presence(&nvidia2, &amd2, &intel2, &ib2, &opa2);

  assert(nvidia1 == nvidia2);
  assert(amd1 == amd2);
  assert(intel1 == intel2);
  assert(ib1 == ib2);
  assert(opa1 == opa2);

  hwdetect_invalidate_probe_cache();
  hwdetect_invalidate_probe_cache();
}

int main(void)
{
  test_invalidate_probe_cache_idempotent();
  printf("test_hwdetect_lspci passed\n");
  return 0;
}
