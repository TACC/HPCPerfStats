#include "amd64_event_tables.h"
#include "amd64_df.h"
#undef KEYS
#include "amd64_pmc.h"

const uint64_t amd64_pmc_events_10h[] = {
	FLOPS,
	MERGE,
	DISPATCH_STALL_CYCLES1,
	DISPATCH_STALL_CYCLES0,
};

const uint64_t amd64_pmc_events_zen[] = {
	FLOPS,
	MERGE,
	BRANCH_INST_RETIRED,
	BRANCH_INST_RETIRED_MISS,
	DISPATCH_STALL_CYCLES1,
	DISPATCH_STALL_CYCLES0,
};

const uint64_t amd64_df_dram_events[] = {
	EVENT_DRAM_CHANNEL_0,
	EVENT_DRAM_CHANNEL_1,
	EVENT_DRAM_CHANNEL_2,
	EVENT_DRAM_CHANNEL_3,
};
