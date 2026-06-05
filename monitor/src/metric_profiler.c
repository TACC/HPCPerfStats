/* metric_profiler.c — aggregate collect timing and top metrics (debug builds). */
#include "metric_profiler.h"

#ifdef MONITOR_METRIC_PROFILER

#include "stats.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef MONITOR_METRIC_PROFILER_EBPF
#include "metric_profiler_ebpf.h"
#endif

#define PROF_MAX_METRICS 4096
#define PROF_MAX_TYPES 128
#define PROF_TOP_K 12
#define PROF_REPORT_EVERY_CYCLES 120ULL

struct metric_entry {
  char type_name[STATS_TYPE_NAME_MAX];
  char dev[32];
  char key[48];
  unsigned long long calls;
  unsigned long long wall_ns_total;
  unsigned long long cpu_ns_total;
  unsigned long long wait_ns_total;
  unsigned long long wall_ns_max;
  unsigned long long wall_ns_min;
};

struct type_entry {
  char type_name[STATS_TYPE_NAME_MAX];
  unsigned long long calls;
  unsigned long long wall_ns_total;
  unsigned long long cpu_ns_total;
  unsigned long long wait_ns_total;
};

static struct metric_entry g_metrics[PROF_MAX_METRICS];
static size_t g_metrics_len;
static unsigned long long g_metrics_overflow;

static struct type_entry g_types[PROF_MAX_TYPES];
static size_t g_types_len;
static unsigned long long g_cycle_count;

static unsigned long long g_collect_wall_ns;
static unsigned long long g_collect_cpu_ns;

#ifdef MONITOR_METRIC_PROFILER_EBPF
static struct metric_profiler_attr_sample g_attr_begin;
#endif

static unsigned long long now_ns(clockid_t cid)
{
  struct timespec ts;
  if (clock_gettime(cid, &ts) != 0)
    return 0;
  return (unsigned long long)ts.tv_sec * 1000000000ULL + (unsigned long long)ts.tv_nsec;
}

static unsigned long long clamp_wait(unsigned long long wall_ns, unsigned long long cpu_ns)
{
  if (wall_ns < cpu_ns)
    return 0;
  return wall_ns - cpu_ns;
}

static struct metric_entry *find_or_add_metric(const char *type_name, const char *dev, const char *key)
{
  size_t i;
  for (i = 0; i < g_metrics_len; i++) {
    if (strcmp(g_metrics[i].type_name, type_name) == 0
        && strcmp(g_metrics[i].dev, dev) == 0
        && strcmp(g_metrics[i].key, key) == 0)
      return &g_metrics[i];
  }
  if (g_metrics_len >= PROF_MAX_METRICS) {
    g_metrics_overflow++;
    return NULL;
  }
  memset(&g_metrics[g_metrics_len], 0, sizeof(g_metrics[g_metrics_len]));
  snprintf(g_metrics[g_metrics_len].type_name, sizeof(g_metrics[g_metrics_len].type_name), "%s", type_name);
  snprintf(g_metrics[g_metrics_len].dev, sizeof(g_metrics[g_metrics_len].dev), "%s", dev);
  snprintf(g_metrics[g_metrics_len].key, sizeof(g_metrics[g_metrics_len].key), "%s", key);
  g_metrics[g_metrics_len].wall_ns_min = ~0ULL;
  g_metrics_len++;
  return &g_metrics[g_metrics_len - 1];
}

static struct type_entry *find_or_add_type(const char *type_name)
{
  size_t i;
  for (i = 0; i < g_types_len; i++) {
    if (strcmp(g_types[i].type_name, type_name) == 0)
      return &g_types[i];
  }
  if (g_types_len >= PROF_MAX_TYPES)
    return NULL;
  memset(&g_types[g_types_len], 0, sizeof(g_types[g_types_len]));
  snprintf(g_types[g_types_len].type_name, sizeof(g_types[g_types_len].type_name), "%s", type_name);
  g_types_len++;
  return &g_types[g_types_len - 1];
}

static int cmp_metric_total_wall_desc(const void *a, const void *b)
{
  const struct metric_entry *const *ma = a;
  const struct metric_entry *const *mb = b;
  if ((*ma)->wall_ns_total < (*mb)->wall_ns_total)
    return 1;
  if ((*ma)->wall_ns_total > (*mb)->wall_ns_total)
    return -1;
  return strcmp((*ma)->key, (*mb)->key);
}

static void emit_report(FILE *out)
{
  size_t i;
  struct metric_entry *top[PROF_MAX_METRICS];
  size_t top_len = g_metrics_len;
  if (out == NULL)
    return;
  for (i = 0; i < top_len; i++)
    top[i] = &g_metrics[i];
  qsort(top, top_len, sizeof(top[0]), cmp_metric_total_wall_desc);

  fprintf(out, "metric-profiler: cycles=%llu tracked_metrics=%zu tracked_types=%zu overflow=%llu\n",
          g_cycle_count, g_metrics_len, g_types_len, g_metrics_overflow);

  for (i = 0; i < g_types_len; i++) {
    const struct type_entry *t = &g_types[i];
    fprintf(out,
            "metric-profiler:type=%s calls=%llu wall_ns=%llu cpu_ns=%llu wait_ns=%llu avg_wall_ns=%llu\n",
            t->type_name, t->calls, t->wall_ns_total, t->cpu_ns_total, t->wait_ns_total,
            t->calls ? t->wall_ns_total / t->calls : 0ULL);
  }

  if (top_len > PROF_TOP_K)
    top_len = PROF_TOP_K;
  for (i = 0; i < top_len; i++) {
    const struct metric_entry *m = top[i];
    fprintf(out,
            "metric-profiler:metric=%s/%s/%s calls=%llu wall_ns=%llu cpu_ns=%llu wait_ns=%llu"
            " avg_wall_ns=%llu min_wall_ns=%llu max_wall_ns=%llu\n",
            m->type_name, m->dev, m->key, m->calls, m->wall_ns_total, m->cpu_ns_total,
            m->wait_ns_total, m->calls ? m->wall_ns_total / m->calls : 0ULL,
            m->wall_ns_min == ~0ULL ? 0ULL : m->wall_ns_min, m->wall_ns_max);
  }
}

void metric_profiler_cycle_begin(void)
{
#ifdef MONITOR_METRIC_PROFILER_EBPF
  metric_profiler_attr_capture(&g_attr_begin);
#endif
}

void metric_profiler_cycle_end(FILE *out)
{
#ifdef MONITOR_METRIC_PROFILER_EBPF
  struct metric_profiler_attr_sample attr_end;
  struct metric_profiler_attr_sample attr_delta;
#endif
  g_cycle_count++;
  if (g_cycle_count % PROF_REPORT_EVERY_CYCLES != 0)
    return;
  emit_report(out);
#ifdef MONITOR_METRIC_PROFILER_EBPF
  if (out == NULL)
    return;
  metric_profiler_attr_capture(&attr_end);
  metric_profiler_attr_delta(&g_attr_begin, &attr_end, &attr_delta);
  fprintf(out, "metric-profiler:backend=ebpf");
  metric_profiler_attr_fprint(out, &attr_delta);
  fprintf(out, "\n");
#endif
}

void metric_profiler_collect_begin(const char *type_name)
{
  (void)type_name;
  g_collect_wall_ns = now_ns(CLOCK_MONOTONIC);
  g_collect_cpu_ns = now_ns(CLOCK_THREAD_CPUTIME_ID);
}

void metric_profiler_collect_end(const char *type_name)
{
  struct type_entry *entry;
  unsigned long long wall_end_ns = now_ns(CLOCK_MONOTONIC);
  unsigned long long cpu_end_ns = now_ns(CLOCK_THREAD_CPUTIME_ID);
  unsigned long long wall_ns = wall_end_ns - g_collect_wall_ns;
  unsigned long long cpu_ns = cpu_end_ns - g_collect_cpu_ns;
  unsigned long long wait_ns = clamp_wait(wall_ns, cpu_ns);

  entry = find_or_add_type(type_name);
  if (entry == NULL)
    return;
  entry->calls++;
  entry->wall_ns_total += wall_ns;
  entry->cpu_ns_total += cpu_ns;
  entry->wait_ns_total += wait_ns;
}

void metric_profiler_record_metric(const char *type_name, const char *dev, const char *key,
                                   unsigned long long wall_ns, unsigned long long cpu_ns)
{
  struct metric_entry *entry;
  unsigned long long wait_ns;
  wait_ns = clamp_wait(wall_ns, cpu_ns);

  entry = find_or_add_metric(type_name, dev, key);
  if (entry == NULL)
    return;
  entry->calls++;
  entry->wall_ns_total += wall_ns;
  entry->cpu_ns_total += cpu_ns;
  entry->wait_ns_total += wait_ns;
  if (wall_ns > entry->wall_ns_max)
    entry->wall_ns_max = wall_ns;
  if (wall_ns < entry->wall_ns_min)
    entry->wall_ns_min = wall_ns;
}

#endif
