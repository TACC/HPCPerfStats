/*! \file likwid_pmc_access_mode.h
 *  HPCPERFSTATS_LIKWID_ACCESS → LIKWID HPM access mode selection.
 */

#ifndef _LIKWID_PMC_ACCESS_MODE_H_
#define _LIKWID_PMC_ACCESS_MODE_H_

typedef enum {
  LIKWID_PMC_ACCESS_PERF = 0,
  LIKWID_PMC_ACCESS_DIRECT = 1,
} likwid_pmc_access_mode_t;

/* Read HPCPERFSTATS_LIKWID_ACCESS (unset/empty/perf → PERF; direct → DIRECT).
 * If the variable is set but unrecognized, sets *invalid to 1 (when non-NULL)
 * and returns PERF. */
likwid_pmc_access_mode_t likwid_pmc_access_mode_from_env(int *invalid);

const char *likwid_pmc_access_mode_name(likwid_pmc_access_mode_t mode);

#endif
