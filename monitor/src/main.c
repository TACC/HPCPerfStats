/* Batch stats client: lock, collect enabled types, append to archive current file. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <getopt.h>
#include <signal.h>
#include <malloc.h>
#include <errno.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include "string1.h"
#include "stats.h"
#include "stats_file.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "pscanf.h"
#include "cpuid.h"
#include "hwdetect.h"
#include "metric_profiler.h"
#include "stats_runtime.h"

struct timeval tp;
double current_time;
char jobid[80] = "0";
int nr_cpus;

int n_pmcs = 0;
processor_t processor = 0;

static void alarm_handler(int sig)
{
  (void)sig;
}

static int open_lock_timeout(const char *path, int timeout)
{
  struct sigaction alarm_action = {
      .sa_handler = &alarm_handler,
  };
  struct flock lock = {
      .l_type = F_WRLCK,
      .l_whence = SEEK_SET,
  };

  if (path_open_is_skipped(path))
    return -1;
  int fd = open(path, O_CREAT | O_RDWR, 0600);
  if (fd < 0) {
    path_open_record_failure_once(path);
    return -1;
  }

  if (sigaction(SIGALRM, &alarm_action, NULL) < 0) {
    ERROR("cannot set alarm handler: %m\n");
    close(fd);
    return -1;
  }

  alarm(timeout);
  if (fcntl(fd, F_SETLKW, &lock) < 0) {
    ERROR("cannot lock `%s': %m\n", path);
    alarm(0);
    close(fd);
    return -1;
  }
  alarm(0);
  return fd;
}

typedef enum {
  main_cmd_begin,
  main_cmd_collect,
  main_cmd_end,
  main_cmd_rotate,
} main_cmd_t;

static main_cmd_t main_parse_command_word(const char *cmd_str)
{
  if (strcmp(cmd_str, "begin") == 0)
    return main_cmd_begin;
  if (strcmp(cmd_str, "collect") == 0)
    return main_cmd_collect;
  if (strcmp(cmd_str, "end") == 0)
    return main_cmd_end;
  if (strcmp(cmd_str, "rotate") == 0)
    return main_cmd_rotate;
  FATAL("invalid command `%s'\n", cmd_str);
}

static int main_maybe_unlink_current_for_rotate(const char *current_path)
{
  if (unlink(current_path) < 0 && errno != ENOENT) {
    ERROR("cannot unlink `%s': %m\n", current_path);
    return 1;
  }
  return 0;
}

static void main_refresh_time_and_topology(void)
{
  gettimeofday(&tp, NULL);
  current_time = tp.tv_sec + tp.tv_usec / 1000000.0;
  pscanf(JOBID_FILE_PATH, "%79s", jobid);
  nr_cpus = sysconf(_SC_NPROCESSORS_ONLN);
  processor = signature(&n_pmcs);
}

static void main_select_types_named_in_argv(char **arg_list, size_t arg_count)
{
  size_t i;
  for (i = 0; i < arg_count; i++) {
    struct stats_type *type = stats_type_get(arg_list[i]);
    if (type == NULL) {
      ERROR("unknown type `%s'\n", arg_list[i]);
      continue;
    }
    type->st_selected = 1;
  }
}

static void main_init_enable_and_collect_types(main_cmd_t cmd, int enable_all, int select_all)
{
  stats_runtime_main_prepare_spec spec = {
      .enable_all = enable_all,
      .select_all = select_all,
      .call_begin = (cmd == main_cmd_begin),
  };

  metric_profiler_cycle_begin();
  stats_runtime_main_prepare_types(&spec);
  stats_runtime_collect_enabled_metrics(1);
  metric_profiler_cycle_end(stderr);
}

static void main_apply_mark_or_jobid(struct stats_file *sf, main_cmd_t cmd, const char *mark,
                                     const char *cmd_str, char **arg_list, size_t arg_count)
{
  if (mark != NULL)
    stats_file_mark(sf, "%s", mark);
  else if (cmd == main_cmd_begin || cmd == main_cmd_end)
    /* Use argv command word (e.g. "rotate" after rotate→begin mapping). */
    stats_file_mark(sf, "%s %s", cmd_str, arg_count > 0 ? arg_list[0] : "-");
}

static void main_destroy_all_types(void)
{
  stats_runtime_teardown();
}

static void usage(void)
{
  fprintf(stderr,
          "Usage: %s [OPTION]... [TYPE]...\n"
          "Collect statistics.\n"
          "\n"
          "Mandatory arguments to long options are mandatory for short options too.\n"
          "  -h, --help         display this help and exit\n"
          /* "  -l, --list-types ...\n" */
          /* describe */
          ,
          program_invocation_short_name);
}

int main(int argc, char *argv[])
{
  const int lock_timeout = 30;
  const char *current_path = STATS_DIR_PATH "/current";
  const char *mark = NULL;
  int rc = 0;

  struct option opts[] = {
      {"help", 0, 0, 'h'},
      {"mark", 0, 0, 'm'},
      {NULL, 0, 0, 0},
  };

  int c;
  while ((c = getopt_long(argc, argv, "hm:", opts, 0)) != -1) {
    switch (c) {
    case 'h':
      usage();
      exit(0);
    case 'm':
      mark = optarg;
      break;
    case '?':
      fprintf(stderr, "Try `%s --help' for more information.\n", program_invocation_short_name);
      exit(1);
    }
  }
  umask(022);

  if (!(optind < argc))
    FATAL("must specify a command\n");

  const char *cmd_str = argv[optind];
  char **arg_list = argv + optind + 1;
  size_t arg_count = (size_t)(argc - optind - 1);

  main_cmd_t cmd = main_parse_command_word(cmd_str);

  int lock_fd = open_lock_timeout(STATS_LOCK_PATH, lock_timeout);
  if (lock_fd < 0)
    FATAL("cannot acquire lock\n");
  (void)lock_fd;

  if (cmd == main_cmd_rotate) {
    rc = main_maybe_unlink_current_for_rotate(current_path);
    cmd = main_cmd_begin;
  }

  main_refresh_time_and_topology();

  if (mkdir(STATS_DIR_PATH, 0777) < 0) {
    if (errno != EEXIST)
      FATAL("cannot create directory `%s': %m\n", STATS_DIR_PATH);
  }

  struct stats_file sf;
  if (stats_file_open(&sf, current_path) < 0) {
    rc = 1;
    goto out;
  }

  int enable_all = 0;
  int select_all = cmd != main_cmd_collect || arg_count == 0;

  if (sf.sf_empty) {
    char *link_path = strf("%s/%ld", STATS_DIR_PATH, (long)current_time);
    if (link_path == NULL)
      ERROR("cannot create path: %m\n");
    else if (link(current_path, link_path) < 0)
      ERROR("cannot link `%s' to `%s': %m\n", current_path, link_path);
    free(link_path);
    enable_all = 1;
    select_all = 1;
  }

  if (cmd == main_cmd_collect)
    main_select_types_named_in_argv(arg_list, arg_count);

  main_init_enable_and_collect_types(cmd, enable_all, select_all);

  main_apply_mark_or_jobid(&sf, cmd, mark, cmd_str, arg_list, arg_count);

  if (stats_file_close(&sf) < 0)
    rc = 1;

  main_destroy_all_types();

out:
  return rc;
}
