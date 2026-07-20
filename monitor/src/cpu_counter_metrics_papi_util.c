/* Pure helpers for PAPI begin hardening (unit-testable, no libpapi). */
#include <sys/resource.h>

#include "cpu_counter_metrics_papi_util.h"

rlim_t papi_desired_nofile_soft(int nr_cpus, int n_active)
{
  unsigned long long need;

  if (nr_cpus <= 0 || n_active <= 0)
    return (rlim_t)1024;
  /* Per-CPU eventset ≈ n_active FDs plus headroom for DCGM/other. */
  need = (unsigned long long)nr_cpus * ((unsigned long long)n_active + 8ULL) + 256ULL;
  if (need < 1024ULL)
    need = 1024ULL;
  /* Hard cap — avoid RLIM_INFINITY arithmetic pitfalls on (rlim_t)-1. */
  if (need > 1048576ULL)
    need = 1048576ULL;
  return (rlim_t)need;
}

int papi_shrink_active_count(int n_active, int hwctrs)
{
  if (n_active <= 0)
    return 0;
  if (hwctrs <= 0)
    return n_active;
  if (n_active <= hwctrs)
    return n_active;
  return hwctrs;
}

int papi_is_partial_attach(int ok_cpus, int nr_cpus)
{
  return (ok_cpus > 0 && nr_cpus > 0 && ok_cpus < nr_cpus);
}
