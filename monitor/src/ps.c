#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
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

static void ps_collect_proc_stat(struct stats *stats)
{
  const char *path = "/proc/stat";
  FILE *file = NULL;
  char file_buf[4096];
  char *line = NULL;
  size_t line_size = 0;

  file = file_fopen_read(path);
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  while (getline(&line, &line_size, file) >= 0) {
    char *key, *rest = line;
    key = wsep(&rest);
    if (key == NULL || rest == NULL)
      continue;

    if (strncmp(key, "cpu", 3) == 0)
      continue;

    if (strcmp(key, "intr") == 0)
      continue;

    errno = 0;
    unsigned long long val = strtoull(rest, NULL, 0);
    if (errno == 0)
      stats_set(stats, key, val);
  }

 out:
  free(line);
  if (file != NULL)
    fclose(file);
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
    /* XXX */
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
  struct stats *stats = NULL;

  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    return;

  ps_collect_proc_stat(stats);
  ps_collect_loadavg(stats);
}

struct stats_type ps_stats_type = {
  .st_name = "ps",
  .st_collect = &ps_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
