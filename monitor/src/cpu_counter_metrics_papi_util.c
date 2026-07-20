/* Pure helpers for PAPI begin hardening (unit-testable, no libpapi). */
#include <stddef.h>
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

int papi_filter_system_wide_events(int *slots, int *codes, int n_active, int filter_mode)
{
  int tmp_slots[8];
  int tmp_codes[8];
  int out = 0;
  int i;
  int want_ins = (filter_mode == PAPI_SW_FILTER_CYC_INS);

  if (slots == NULL || codes == NULL || n_active <= 0)
    return 0;
  if (n_active > 8)
    n_active = 8;

  for (i = 0; i < n_active; i++) {
    if (slots[i] == PAPI_UTIL_SLOT_CYC) {
      tmp_slots[out] = slots[i];
      tmp_codes[out] = codes[i];
      out++;
      break;
    }
  }
  if (out == 0)
    return 0;

  if (want_ins) {
    for (i = 0; i < n_active; i++) {
      if (slots[i] == PAPI_UTIL_SLOT_INS) {
        tmp_slots[out] = slots[i];
        tmp_codes[out] = codes[i];
        out++;
        break;
      }
    }
  }

  for (i = 0; i < out; i++) {
    slots[i] = tmp_slots[i];
    codes[i] = tmp_codes[i];
  }
  return out;
}

int papi_census_min_nonzero_cyc(int nr_cpus)
{
  int eighth;

  if (nr_cpus <= 0)
    return 1;
  eighth = nr_cpus / 8;
  if (eighth < 4)
    return 4;
  return eighth;
}

int papi_census_needs_reshrink(int nonzero_cyc, int ok_cpus, int nr_cpus)
{
  int min_nz;
  int attach_floor;

  if (ok_cpus <= 0 || nr_cpus <= 0)
    return 0;
  /* Require attach ≈ full (at least 7/8 of CPUs). */
  attach_floor = (nr_cpus * 7) / 8;
  if (attach_floor < 1)
    attach_floor = 1;
  if (ok_cpus < attach_floor)
    return 0;
  min_nz = papi_census_min_nonzero_cyc(nr_cpus);
  return (nonzero_cyc < min_nz) ? 1 : 0;
}
