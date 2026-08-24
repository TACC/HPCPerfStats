/*! \file likwid_pmc_adapter.h
 *  LIKWID perfmon adapter for host_cpu_hw / amd_x86_pmc sampling.
 */

#ifndef _LIKWID_PMC_ADAPTER_H_
#define _LIKWID_PMC_ADAPTER_H_

#include <stdint.h>
#include "stats.h"

/* Default LIKWID_FORCE=1 if unset (C-API equivalent of likwid-perfctr -f).
 * Operators may set LIKWID_FORCE=0 before init to opt out of PMC overwrite. */
void likwid_pmc_adapter_ensure_force_env(void);

int likwid_pmc_adapter_init(int nr_threads);
void likwid_pmc_adapter_finalize(void);
int likwid_pmc_adapter_setup_events(const char *event_string);
/*
 * Once per host_cpu_hw tick on x86: setupCounters(g_group)+startCounters after
 * DF/IMC/RAPL steal. On aarch64/ARM there is no LIKWID uncore steal — skip
 * re-arm so PERF keeps a lifetime window for schema E keys.
 */
int likwid_pmc_adapter_prepare_collect(void);
/* One perfmon_readGroupCounters after prepare_collect; overlay then peeks per CPU. */
int likwid_pmc_adapter_read_group(void);
int likwid_pmc_adapter_read_cpu(struct stats *stats, int cpu, uint64_t *events, int nr_events,
                                int max_ctrs);
/* Overlay: extract measured cycles/instr without writing DCGM fail-soft keys.
 * Caller must have a fresh likwid_pmc_adapter_read_group() this tick. */
int likwid_pmc_adapter_read_cpu_cycles_instr(int cpu, unsigned long long *cycles_out,
                                             unsigned long long *instr_out);

#endif
