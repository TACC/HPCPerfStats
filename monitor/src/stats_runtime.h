#ifndef STATS_RUNTIME_H
#define STATS_RUNTIME_H

#include <stddef.h>
#include <stdio.h>

#include "collect_tier.h"
#include "stats_sink.h"

struct stats;
struct stats_type;

typedef struct stats_runtime_main_prepare_spec {
  int enable_all;
  int select_all;
  int call_begin;
} stats_runtime_main_prepare_spec;

void stats_runtime_teardown(void);

void stats_runtime_daemon_prepare_types(void);
void stats_runtime_daemon_reset_types(void);
int stats_runtime_daemon_ensure_types(void);
void stats_runtime_daemon_set_type_controls(const char *profile, const char *disable_csv);

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
                                const struct stats_sink_ops *sink, int require_selected);

/* Two-tier collect phase control (thin wrappers over collect_tier). */
void stats_runtime_set_collect_phase(enum collect_phase phase);
enum collect_phase stats_runtime_effective_collect_phase(int write_hdr);
int stats_schema_key_active_this_phase(const struct stats_type *type, int idx);

/*! Decide and apply the collect phase for one fast-timer tick. Updates
 *  *last_slow_slot and the global phase; returns the chosen phase. Pass
 *  *last_slow_slot < 0 on the first tick to force a full (slow) collection. */
enum collect_phase stats_runtime_collect_phase_for_tick(double now_sec, long long *last_slow_slot,
                                                        double sample_freq_slow);

/*! Join enabled collector st_name values into buf (comma-separated).
 *  Returns bytes written excluding NUL, or -1 on truncation/NULL buf. */
int stats_runtime_format_enabled_type_names(char *buf, size_t cap);

/*! Join disabled collector st_name values into buf (comma-separated).
 *  Returns bytes written excluding NUL, or -1 on truncation/NULL buf. */
int stats_runtime_format_disabled_type_names(char *buf, size_t cap);

#endif
