/*! \file likwid_rapl_pwr.h
 *  RAPL via LIKWID PWR* perfmon events (ACCESSMODE_PERF / power PMU).
 */

#ifndef _LIKWID_RAPL_PWR_H_
#define _LIKWID_RAPL_PWR_H_

#include <stdint.h>

/* Intel SPR/SKX-class: pkg + pp0 + pp1 + dram (LIKWID PWR0–PWR3). */
const char *likwid_rapl_pwr_intel_eventset(void);

/* AMD Zen: package energy only (PWR0). */
const char *likwid_rapl_pwr_amd_eventset(void);

/*
 * Map LIKWID event name to schema key.
 * amd_path nonzero → PWR_PP0 maps to core_energy; else pp0_energy.
 * Returns NULL when the event is not a RAPL energy counter.
 */
const char *likwid_rapl_pwr_schema_key_from_event(const char *event_name, int amd_path);

/* Convert LIKWID PWR result (Joules) to schema millijoules. */
unsigned long long likwid_rapl_joules_to_mj(double joules);

/* Nonzero when joules is finite and strictly positive (reject flat-zero/NaN). */
int likwid_rapl_pwr_result_usable(double joules);

/* Add/setup PWR eventset after host_cpu_hw LIKWID session is ready.
 * Succeeds when PWR eventset works and/or powercap energy_uj is available. */
int likwid_rapl_pwr_begin(int amd_path);

int likwid_rapl_pwr_ready(void);

int likwid_rapl_pwr_collect_socket_mj(int cpu_id, unsigned int socket_id,
                                      unsigned long long *pkg_mj, unsigned long long *core_mj,
                                      unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                      int *has_dram, unsigned long long *pp1_mj, int *has_pp1);

#endif
