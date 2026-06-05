/* Process-wide uname(2) cache used by stats_buffer header/sample emission. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats_buffer_uts.h"

static void test_ensure_and_cached_uts_non_null(void)
{
  const struct utsname *uts;

  stats_buffer_uts_cache_reset();
  stats_buffer_ensure_uts_cached();
  uts = stats_buffer_cached_uts();
  assert(uts != NULL);
  assert(uts->sysname[0] != '\0');
}

static void test_reset_invalidates_cache(void)
{
  const struct utsname *before;
  const struct utsname *after;

  stats_buffer_uts_cache_reset();
  stats_buffer_ensure_uts_cached();
  before = stats_buffer_cached_uts();
  assert(before != NULL);

  stats_buffer_uts_cache_reset();
  stats_buffer_ensure_uts_cached();
  after = stats_buffer_cached_uts();
  assert(after != NULL);
  assert(strcmp(before->sysname, after->sysname) == 0);
}

int main(void)
{
  test_ensure_and_cached_uts_non_null();
  test_reset_invalidates_cache();
  printf("test_stats_buffer_uts passed\n");
  return 0;
}
