/*!
 \file intel_rapl.c
 \author Todd Evans
 \brief RAPL Counters for Intel Processors
*/

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "likwid_rapl.h"
#include "rapl_likwid_stats.h"

#define KEYS                                                                  \
	X(MSR_PKG_ENERGY_STATUS, "E,W=32,U=mJ", ""),                          \
	    X(MSR_PP0_ENERGY_STATUS, "E,W=32,U=mJ", ""),                      \
	    X(MSR_PP1_ENERGY_STATUS, "E,W=32,U=mJ", ""),                      \
	    X(MSR_DRAM_ENERGY_STATUS, "E,W=32,U=mJ", "")

static int intel_rapl_begin(struct stats_type *type)
{
	if (!likwid_rapl_is_supported_processor()) {
		TRACE("intel_rapl disabled because processor is not LIKWID RAPL capable\n");
		type->st_enabled = 0;
		return -1;
	}
	return 0;
}

static void intel_rapl_collect(struct stats_type *type)
{
	int i;

	for (i = 0; i < nr_cpus; i++) {
		char cpu[80];
		int pkg_id = -1;
		int core_id = -1;
		int smt_id = -1;
		int nr_cores = 0;
		char pkg[80];

		snprintf(cpu, sizeof(cpu), "%d", i);
		cpuid_read_cpu_topology(cpu, &pkg_id, &core_id, &smt_id,
					&nr_cores);

		if (core_id == 0 && smt_id == 0) {
			snprintf(pkg, sizeof(pkg), "%d", pkg_id);
			rapl_likwid_intel_collect_pkg(type, pkg, atoi(cpu),
							(unsigned)pkg_id);
		}
	}
}

struct stats_type intel_rapl_stats_type = {
    .st_name = "intel_rapl",
    .st_begin = &intel_rapl_begin,
    .st_collect = &intel_rapl_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
