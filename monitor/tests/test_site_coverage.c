/* Linked into the -DDEBUG fault-coverage twins of the collector sentinel tests: at a normal exit,
 * every stats_set_missing* call site registered in the binary must have fired, else exit 1.
 * Reads the stats_sentinel_sites section directly, so it needs neither stats.c nor
 * stats_sentinel_debug.c (the twins link the stubs their base test uses). */
#include <stdio.h>
#include <unistd.h>

#include "stats_sentinel_debug.h"

extern struct stats_sentinel_site *__start_stats_sentinel_sites[] __attribute__((weak));
extern struct stats_sentinel_site *__stop_stats_sentinel_sites[] __attribute__((weak));

static void test_site_coverage_check(void) __attribute__((destructor));

static void test_site_coverage_check(void)
{
  size_t sites = 0;
  size_t never = 0;

  if (__start_stats_sentinel_sites == NULL)
    return;
  for (struct stats_sentinel_site **s = __start_stats_sentinel_sites;
       s < __stop_stats_sentinel_sites; s++) {
    sites++;
    if ((*s)->hits == 0) {
      fprintf(stderr, "site coverage: %s:%d never fired\n", (*s)->file, (*s)->line);
      never++;
    }
  }
  if (never != 0) {
    fprintf(stderr, "site coverage: %zu of %zu failure site(s) never fired\n", never, sites);
    fflush(NULL);
    _exit(1);
  }
  printf("site coverage: all %zu failure site(s) fired\n", sites);
}
