#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "stats.h"
#include "stats_runtime.h"

static struct stats_type g_types[2];
static int g_init_calls[2];
static int g_destroy_calls[2];

static int find_type_index(struct stats_type *type)
{
	if (type == &g_types[0])
		return 0;
	if (type == &g_types[1])
		return 1;
	return -1;
}

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}
void auto_disable_optional_stats_by_lspci(void) {}
void metric_profiler_collect_begin(const char *name) {(void)name;}
void metric_profiler_collect_end(const char *name) {(void)name;}
void metric_profiler_cycle_begin(void) {}
void metric_profiler_cycle_end(FILE *stream) {(void)stream;}
void monitor_log_error(const char *fmt, ...) {(void)fmt;}
void monitor_log_warn(const char *fmt, ...) {(void)fmt;}

int stats_type_init(struct stats_type *type)
{
	int idx = find_type_index(type);
	assert(idx >= 0);
	g_init_calls[idx]++;
	return 0;
}

void stats_type_destroy(struct stats_type *type)
{
	int idx = find_type_index(type);
	assert(idx >= 0);
	g_destroy_calls[idx]++;
}

struct stats_type *stats_type_for_each(size_t *i)
{
	if (*i >= 2)
		return NULL;
	return &g_types[(*i)++];
}

struct stats_type *stats_type_get(const char *name)
{
	size_t i;
	for (i = 0; i < 2; i++) {
		if (strcmp(g_types[i].st_name, name) == 0)
			return &g_types[i];
	}
	return NULL;
}

static void reset_state(void)
{
	memset(g_types, 0, sizeof(g_types));
	memset(g_init_calls, 0, sizeof(g_init_calls));
	memset(g_destroy_calls, 0, sizeof(g_destroy_calls));
	snprintf(g_types[0].st_name, sizeof(g_types[0].st_name), "%s", "host_proc");
	snprintf(g_types[1].st_name, sizeof(g_types[1].st_name), "%s", "host_net");
	unsetenv("HPCPERFSTATS_DISABLE_TYPES");
	stats_runtime_daemon_set_type_controls("default", NULL);
	stats_runtime_daemon_reset_types();
}

static void test_minimal_profile_disables_proc(void)
{
	reset_state();
	stats_runtime_daemon_set_type_controls("minimal", NULL);
	stats_runtime_daemon_prepare_types();
	assert(g_types[0].st_enabled == 0);
	assert(g_types[1].st_enabled == 1);
	assert(g_init_calls[0] == 0);
	assert(g_init_calls[1] == 1);
	stats_runtime_daemon_reset_types();
}

static void test_disable_csv_disables_named_type(void)
{
	reset_state();
	stats_runtime_daemon_set_type_controls("default", " host_net ");
	stats_runtime_daemon_prepare_types();
	assert(g_types[0].st_enabled == 1);
	assert(g_types[1].st_enabled == 0);
	assert(g_init_calls[0] == 1);
	assert(g_init_calls[1] == 0);
	stats_runtime_daemon_reset_types();
}

static void test_env_disable_types_applies(void)
{
	reset_state();
	assert(setenv("HPCPERFSTATS_DISABLE_TYPES", "host_proc,host_net", 1) == 0);
	stats_runtime_daemon_prepare_types();
	assert(g_types[0].st_enabled == 0);
	assert(g_types[1].st_enabled == 0);
	assert(g_init_calls[0] == 0);
	assert(g_init_calls[1] == 0);
	stats_runtime_daemon_reset_types();
}

int main(void)
{
	test_minimal_profile_disables_proc();
	test_disable_csv_disables_named_type();
	test_env_disable_types_applies();
	printf("test_stats_runtime_type_controls passed\n");
	return 0;
}
