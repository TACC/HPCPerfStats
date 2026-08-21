/*! \file likwid_pmc_access_mode.h
 *  HPCPERFSTATS_LIKWID_ACCESS → LIKWID HPM access mode (PERF only).
 */

#ifndef _LIKWID_PMC_ACCESS_MODE_H_
#define _LIKWID_PMC_ACCESS_MODE_H_

typedef enum {
  LIKWID_PMC_ACCESS_PERF = 0,
} likwid_pmc_access_mode_t;

/* Values written to *env_status from likwid_pmc_access_mode_from_env. */
#define LIKWID_PMC_ACCESS_ENV_OK 0
#define LIKWID_PMC_ACCESS_ENV_INVALID 1
#define LIKWID_PMC_ACCESS_ENV_DIRECT_REMOVED 2

/* Always returns PERF. DIRECT MSR access is removed: if the env requests
 * direct, *env_status is LIKWID_PMC_ACCESS_ENV_DIRECT_REMOVED. Unrecognized
 * non-empty values set LIKWID_PMC_ACCESS_ENV_INVALID. env_status may be NULL. */
likwid_pmc_access_mode_t likwid_pmc_access_mode_from_env(int *env_status);

const char *likwid_pmc_access_mode_name(likwid_pmc_access_mode_t mode);

#endif
