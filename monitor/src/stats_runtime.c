#include "stats_runtime.h"

#include "collect.h"
#include "hwdetect.h"
#include "metric_profiler.h"
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"
#include <time.h>

static int g_daemon_types_ready;

static long long stats_runtime_monotonic_us(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return -1;
	return (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
}

void stats_runtime_teardown(void)
{
	size_t i = 0;
	struct stats_type *type;

	cpu_stats_invalidate_file_caches();
	net_stats_invalidate_iface_cache();
	while ((type = stats_type_for_each(&i)) != NULL)
		stats_type_destroy(type);
	g_daemon_types_ready = 0;
}

void stats_runtime_daemon_prepare_types(void)
{
	size_t i = 0;
	struct stats_type *type;

	while ((type = stats_type_for_each(&i)) != NULL)
		type->st_enabled = 1;

	auto_disable_optional_stats_by_lspci();

	i = 0;
	while ((type = stats_type_for_each(&i)) != NULL) {
		if (!type->st_enabled)
			continue;
		if (stats_type_init(type) < 0) {
			monitor_log_error("stats_runtime: disabling `%s` due to init failure\n",
					  type->st_name);
			type->st_enabled = 0;
			continue;
		}
		if (type->st_begin != NULL)
			(*type->st_begin)(type);
	}
	g_daemon_types_ready = 1;
}

void stats_runtime_daemon_reset_types(void)
{
	if (!g_daemon_types_ready)
		return;
	stats_runtime_teardown();
}

int stats_runtime_daemon_ensure_types(void)
{
	long long started_us;
	long long elapsed_us;

	if (g_daemon_types_ready)
		return 0;
	started_us = stats_runtime_monotonic_us();
	stats_runtime_daemon_prepare_types();
	if (started_us > 0) {
		elapsed_us = stats_runtime_monotonic_us() - started_us;
		if (elapsed_us > 50000LL)
			TRACE("stats_runtime daemon prepare slow: elapsed_us=%lld\n", elapsed_us);
	}
	return 0;
}

void stats_runtime_collect_enabled_metrics(int require_selected)
{
	size_t i = 0;
	struct stats_type *type;

	while ((type = stats_type_for_each(&i)) != NULL) {
		if (!type->st_enabled)
			continue;
		if (require_selected && !type->st_selected)
			continue;
		metric_profiler_collect_begin(type->st_name);
		(*type->st_collect)(type);
		metric_profiler_collect_end(type->st_name);
	}
}

void stats_runtime_main_prepare_types(const stats_runtime_main_prepare_spec *spec)
{
	size_t i = 0;
	struct stats_type *type;

	auto_disable_optional_stats_by_lspci();

	while ((type = stats_type_for_each(&i)) != NULL) {
		if (spec->enable_all)
			type->st_enabled = 1;
	}

	i = 0;
	while ((type = stats_type_for_each(&i)) != NULL) {
		if (!type->st_enabled)
			continue;
		if (stats_type_init(type) < 0) {
			monitor_log_error("stats_runtime: disabling `%s` due to init failure\n",
					  type->st_name);
			type->st_enabled = 0;
			continue;
		}
		if (spec->select_all)
			type->st_selected = 1;
		if (spec->call_begin && type->st_begin != NULL)
			(*type->st_begin)(type);
	}
}

int stats_runtime_collect_cycle(FILE *profiler_stream, void *opaque,
				const struct stats_sink_ops *sink,
				int require_selected)
{
	int rc = 0;
	FILE *prof_out = profiler_stream != NULL ? profiler_stream : stderr;

	metric_profiler_cycle_begin();

	stats_runtime_collect_enabled_metrics(require_selected);

	if (sink != NULL && sink->finalize != NULL)
		rc = sink->finalize(opaque);

	metric_profiler_cycle_end(prof_out);
	return rc;
}
