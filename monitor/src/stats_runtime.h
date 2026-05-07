#ifndef STATS_RUNTIME_H
#define STATS_RUNTIME_H

#include <stdio.h>

#include "stats_sink.h"

typedef struct stats_runtime_main_prepare_spec {
	int enable_all;
	int select_all;
	int call_begin;
} stats_runtime_main_prepare_spec;

void stats_runtime_teardown(void);

void stats_runtime_daemon_prepare_types(void);
void stats_runtime_daemon_reset_types(void);
int stats_runtime_daemon_ensure_types(void);

void stats_runtime_main_prepare_types(const stats_runtime_main_prepare_spec *spec);

/*! Collect metrics for enabled types (optionally requiring st_selected), wrapped with
 *  metric_profiler_collect_begin/end per type. Does not open/close the profiler cycle. */
void stats_runtime_collect_enabled_metrics(int require_selected);

/*! Iterate enabled types (and optionally require st_selected), run metric_profiler
 *  wrappers, then sink finalize (e.g. stats_buffer_collect).
 *
 * \param profiler_stream passed to metric_profiler_cycle_end; if NULL, stderr is used.
 * \param sink may be NULL or sink->finalize may be NULL.
 */
int stats_runtime_collect_cycle(FILE *profiler_stream, void *opaque,
				const struct stats_sink_ops *sink,
				int require_selected);

#endif
