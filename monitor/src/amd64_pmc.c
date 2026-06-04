#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include "stats.h"
#include "trace.h"
#include "msr_io.h"
#include "cpuid.h"
#include "amd64_pmc.h"
#include "amd64_event_tables.h"
#include "amd64_pmu_core.h"

static int amd64_pmc_begin_cpu(char *cpu)
{
	const uint64_t *events = NULL;

	switch (processor) {

	case AMD_10H:
#ifndef MONITOR_LEGACY_PMCS
		ERROR("AMD Family 10h PMC programming requires building with --enable-legacy-pmcs\n");
		return -1;
#else
		events = amd64_pmc_events_10h;
		break;
#endif
	case AMD_17H:
	case AMD_19H:
		events = amd64_pmc_events_zen;
		break;
	default:
		ERROR("Processor model/family %d not supported\n", processor);
		return -1;
	}

	return amd64_pmu_core_program_counters_with_hwcr(cpu, events, n_pmcs);
}

static void amd64_pmc_collect_cpu(struct stats_type *type, char *cpu)
{
	int msr_fd = -1;
	struct stats *stats = NULL;
	static const uint64_t ctr_msrs[6] = {
		MSR_PERF_CTR0, MSR_PERF_CTR1, MSR_PERF_CTR2,
		MSR_PERF_CTR3, MSR_PERF_CTR4, MSR_PERF_CTR5
	};
	static const char *const zen_ctr_keys[6] = {
		"FLOPS", "MERGE", "BRANCH_INST_RETIRED",
		"BRANCH_INST_RETIRED_MISS", "DISPATCH_STALL_CYCLES1",
		"DISPATCH_STALL_CYCLES0"
	};
	static const char *const legacy_ctr_keys[4] = {
		"FLOPS", "MERGE", "DISPATCH_STALL_CYCLES1",
		"DISPATCH_STALL_CYCLES0"
	};
	const char *const *ctr_keys = zen_ctr_keys;
	int n_ctr_keys = 6;
	int i;

	stats = get_current_stats(type, cpu);
	if (stats == NULL)
		goto out;

	msr_fd = msr_open_cpu(cpu, O_RDONLY);
	if (msr_fd < 0)
		goto out;

	if (processor == AMD_10H) {
		ctr_keys = legacy_ctr_keys;
		n_ctr_keys = 4;
	}
	for (i = 0; i < n_ctr_keys; i++) {
		uint64_t val = 0;
		if (msr_read_u64(msr_fd, ctr_msrs[i], &val) < 0)
			TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n",
			      ctr_keys[i], (unsigned int)ctr_msrs[i], cpu);
		else
			stats_set(stats, ctr_keys[i], val);
	}
	for (; i < 6; i++)
		stats_set(stats, zen_ctr_keys[i], 0);
	{
		uint64_t val = 0;
		if (msr_read_u64(msr_fd, MSR_PERF_INST_RETIRED, &val) == 0)
			stats_set(stats, "INST_RETIRED", val);
		if (msr_read_u64(msr_fd, MSR_PERF_APERF, &val) == 0)
			stats_set(stats, "APERF", val);
		if (msr_read_u64(msr_fd, MSR_PERF_MPERF, &val) == 0)
			stats_set(stats, "MPERF", val);
	}

out:
	if (msr_fd >= 0)
		close(msr_fd);
}

static void amd64_pmc_collect(struct stats_type *type)
{
	int i;

	for (i = 0; i < nr_cpus; i++) {
		char cpu[80];

		snprintf(cpu, sizeof(cpu), "%d", i);
		amd64_pmc_collect_cpu(type, cpu);
	}
}

static int amd64_pmc_begin(struct stats_type *type)
{
	int nr = 0;
	int i;

	for (i = 0; i < nr_cpus; i++) {
		char cpu[80];

		snprintf(cpu, sizeof(cpu), "%d", i);
		if (amd64_pmc_begin_cpu(cpu) == 0)
			nr++;
	}

	if (nr == 0)
		type->st_enabled = 0;
	return nr > 0 ? 0 : -1;
}

struct stats_type amd64_pmc_stats_type = {
    .st_name = "amd64_pmc",
    .st_begin = &amd64_pmc_begin,
    .st_collect = &amd64_pmc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
