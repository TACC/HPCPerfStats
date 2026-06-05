/* host_cpu — per-CPU jiffies from /proc/stat. */
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include "stats.h"
#include "collect.h"
#include "fileio.h"
#include "path_open_fail_once.h"
#include "trace.h"
#include "string1.h"

/* One stdio stream for /proc/stat: rewind each sample instead of open/fclose. */
static FILE *g_cpu_proc_stat;
static char g_cpu_proc_stat_io_buf[4096];

void cpu_stats_invalidate_file_caches(void)
{
  if (g_cpu_proc_stat != NULL) {
    fclose(g_cpu_proc_stat);
    g_cpu_proc_stat = NULL;
  }
}

/* The /proc manpage says units are units of 1/sysconf(_SC_CLK_TCK)
   seconds.  sysconf(_SC_CLK_TCK) seems to always be 100. */

/* We ignore steal and guest. */

#define KEYS \
  X(user,    "E,U=cs", "time in user mode"), \
  X(nice,    "E,U=cs", "time in user mode with low priority"), \
  X(system,  "E,U=cs", "time in system mode"), \
  X(idle,    "E,U=cs", "time in idle task"), \
  X(iowait,  "E,U=cs", "time in I/O wait"), \
  X(irq,     "E,U=cs", "time in IRQ"), \
  X(softirq, "E,U=cs", "time in softIRQ")

static void cpu_collect(struct stats_type *type)
{
  const char *path = "/proc/stat";
  FILE *file;
  char *line = NULL;
  size_t line_size = 0;

  if (g_cpu_proc_stat == NULL) {
    if (path_open_is_skipped(path))
      goto out;
    g_cpu_proc_stat = file_fopen_read(path);
    if (g_cpu_proc_stat == NULL) {
      path_open_record_failure_once(path);
      goto out;
    }
    setvbuf(g_cpu_proc_stat, g_cpu_proc_stat_io_buf, _IOFBF, sizeof(g_cpu_proc_stat_io_buf));
  } else {
    rewind(g_cpu_proc_stat);
    clearerr(g_cpu_proc_stat);
  }

  file = g_cpu_proc_stat;

  while (getline(&line, &line_size, file) >= 0) {
    char *rest = line;
    char *cpu = wsep(&rest);
    if (cpu == NULL || rest == NULL)
      continue;

    if (strncmp(cpu, "cpu", 3) != 0)
      continue;

    cpu += 3;

    if (!isdigit(*cpu))
      continue;

    struct stats *stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;

#define X(k,r...) #k
    str_collect_key_list(rest, stats, KEYS, NULL);
#undef X
  }

 out:
  free(line);
}

struct stats_type cpu_stats_type = {
  .st_name = "host_cpu",
  .st_collect = &cpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
