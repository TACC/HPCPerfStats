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
#include "amd64_df.h"
#include "amd64_event_tables.h"
#include "amd64_pmu_core.h"

static int amd64_df_begin_cpu(char *cpu)
{
	switch (processor) {

	case AMD_17H:
	case AMD_19H:
		break;
	default:
		TRACE("Processor model/family %d not supported\n", processor);
		return -1;
	}

	return amd64_pmu_msr_program_selects(cpu, MSR_DF_CTL0,
					   amd64_df_dram_events, 4);
}

static void amd64_df_collect_cpu(struct stats_type *type, char *cpu)
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
		if (msr_read_u64(msr_fd, MSR_DF_##k, &val) < 0)                 \
			TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n", #k, \
			      MSR_DF_##k, cpu);                                 \
		else                                                            \
			stats_set(stats, #k, val);                              \
	})
	KEYS;
#undef X

out:
	if (msr_fd >= 0)
		close(msr_fd);
}

static void amd64_df_collect(struct stats_type *type)
{
	int i;

	for (i = 0; i < nr_cpus; i++) {
		char cpu[80];
		int pkg, core, smt, nr_core;

		snprintf(cpu, sizeof(cpu), "%d", i);

		if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) &&
		    (core == 0) && (smt == 0))
			amd64_df_collect_cpu(type, cpu);
	}
}

static int amd64_df_begin(struct stats_type *type)
{
	int nr = 0;
	int i;

	for (i = 0; i < nr_cpus; i++) {
		char cpu[80];
		int pkg, core, smt, nr_core;

		snprintf(cpu, sizeof(cpu), "%d", i);

		if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) &&
		    (core == 0) && (smt == 0)) {
			if (amd64_df_begin_cpu(cpu) == 0)
				nr++;
		}
	}

	if (nr == 0)
		type->st_enabled = 0;
	return nr > 0 ? 0 : -1;
}

struct stats_type amd64_df_stats_type = {
    .st_name = "amd64_df",
    .st_begin = &amd64_df_begin,
    .st_collect = &amd64_df_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
