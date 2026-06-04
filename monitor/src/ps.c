#include <stddef.h>
#include <string.h>
#include "stats.h"
#include "procfile_parse.h"
#include "procfile_kv.h"
#include "trace.h"
#include "pscanf.h"

// $ cat /proc/stat
// cpu ...
// ...
// intr ...
// ctxt 15088509272
// btime 1288194676
// processes 2591587 /* nr_forks */
// procs_running 17
// procs_blocked 0

#define KEYS \
  X(ctxt, "E", "context switches"), \
  X(processes, "E", "forks"), \
  X(load_1, "", "1 minute load average (* 100)"), \
  X(load_5, "", "5 minute load average (* 100)"), \
  X(load_15, "", "15 minute load average (* 100)"), \
  X(nr_running, "", ""), \
  X(nr_threads, "", "")

static int ps_stat_line_cb(char *line, void *ctx)
{
  struct stats *stats = (struct stats *)ctx;

  /* Skip per-cpu and aggregate cpu lines (handled by cpu.c) and the
   * verbose interrupt line. */
  if (strncmp(line, "cpu", 3) == 0)
    return 0;
  if (strncmp(line, "intr", 4) == 0 &&
      (line[4] == ' ' || line[4] == '\t' || line[4] == '\0'))
    return 0;

  proc_kv_into_stats(stats, line);
  return 0;
}

static void ps_collect_proc_stat(struct stats *stats)
{
  procfile_for_each_line("/proc/stat", ps_stat_line_cb, stats);
}

static void ps_collect_loadavg(struct stats *stats)
{
  const char *path = "/proc/loadavg";
  unsigned long long load[3][2];
  unsigned long long nr_running = 0, nr_threads = 0;

  memset(load, 0, sizeof(load));

  /* Ignore last_pid (sixth field). */
  if (pscanf(path, "%llu.%llu %llu.%llu %llu.%llu %llu/%llu",
             &load[0][0], &load[0][1],
             &load[1][0], &load[1][1],
             &load[2][0], &load[2][1],
             &nr_running, &nr_threads) != 8) {
    TRACE("ps: short or malformed `%s'\n", path);
    return;
  }

  stats_set(stats, "load_1",  load[0][0] * 100 + load[0][1]);
  stats_set(stats, "load_5",  load[1][0] * 100 + load[1][1]);
  stats_set(stats, "load_15", load[2][0] * 100 + load[2][1]);
  stats_set(stats, "nr_running", nr_running);
  stats_set(stats, "nr_threads", nr_threads);
}

static void ps_collect(struct stats_type *type)
{
  struct stats *stats = get_current_stats(type, NULL);

  if (stats == NULL)
    return;

  ps_collect_proc_stat(stats);
  ps_collect_loadavg(stats);
}

struct stats_type ps_stats_type = {
  .st_name = "host_ps",
  .st_collect = &ps_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
