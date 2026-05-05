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

	stats = get_current_stats(type, cpu);
	if (stats == NULL)
		goto out;

	msr_fd = msr_open_cpu(cpu, O_RDONLY);
	if (msr_fd < 0)
		goto out;

#define X(k, r...)                                                              \
	({                                                                      \
		uint64_t val = 0;                                               \
		if (msr_read_u64(msr_fd, MSR_PERF_##k, &val) < 0)               \
			TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n", #k, \
			      MSR_PERF_##k, cpu);                                 \
		else                                                            \
			stats_set(stats, #k, val);                              \
	})
	KEYS;
#undef X

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
