#include <stdio.h>
#include <stdlib.h>
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
#include "pscanf.h"
#include "cpuid.h"
#include "hwdetect.h"

struct timeval tp;
double current_time;
char jobid[80] = "0";
int nr_cpus;

int n_pmcs = 0;
processor_t processor = 0;

static void alarm_handler(int sig)
{
}

static int open_lock_timeout(const char *path, int timeout)
{
  int fd = -1;
  struct sigaction alarm_action = {
    .sa_handler = &alarm_handler,
  };
  struct flock lock = {
    .l_type = F_WRLCK,
    .l_whence = SEEK_SET,
  };

  fd = open(path, O_CREAT|O_RDWR, 0600);
  if (fd < 0) {
    ERROR("cannot open `%s': %m\n", path);
    goto err;
  }

  if (sigaction(SIGALRM, &alarm_action, NULL) < 0) {
    ERROR("cannot set alarm handler: %m\n");
    goto err;
  }

  // Set timer to wait until signal SIGALRM is sent
  alarm(timeout);

  // Wait until any conflicting lock on the file is released. 
  // If alarm signal (SIGALRM) is caught then go to signal 
  // handler set in sigaction structure.
  if (fcntl(fd, F_SETLKW, &lock) < 0) {
    ERROR("cannot lock `%s': %m\n", path);
    goto err;
  }

  if (0) {
  err:
    if (fd >= 0)
      close(fd);
    fd = -1;
  }
  alarm(0);
  return fd;
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
  int lock_fd = -1;
  int lock_timeout = 30;
  const char *current_path = STATS_DIR_PATH"/current";
  const char *mark = NULL;
  int rc = 0;

  struct option opts[] = {
    { "help", 0, 0, 'h' },
    { "mark", 0, 0, 'm' },
    { NULL, 0, 0, 0 },
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
  size_t arg_count = argc - optind - 1;

  enum {
    cmd_begin,
    cmd_collect,
    cmd_end,
    cmd_rotate,
  } cmd;

  if (strcmp(cmd_str, "begin") == 0)
    cmd = cmd_begin;
  else if (strcmp(cmd_str, "collect") == 0)
    cmd = cmd_collect;
  else if (strcmp(cmd_str, "end") == 0)
    cmd = cmd_end;
  else if (strcmp(cmd_str, "rotate") == 0)
    cmd = cmd_rotate;
  else
    FATAL("invalid command `%s'\n", cmd_str);

  // Ensures only one hpcperfstats is running at any time
  lock_fd = open_lock_timeout(STATS_LOCK_PATH, lock_timeout);
  if (lock_fd < 0)
    FATAL("cannot acquire lock\n");

  if (cmd == cmd_rotate) {
    if (unlink(current_path) < 0 && errno != ENOENT) {
        ERROR("cannot unlink `%s': %m\n", current_path);
        rc = 1;
    }
    cmd = cmd_begin;
  }

  gettimeofday(&tp,NULL);
  current_time = tp.tv_sec+tp.tv_usec/1000000.0;
  pscanf(JOBID_FILE_PATH, "%79s", jobid);
  nr_cpus = sysconf(_SC_NPROCESSORS_ONLN);
  processor = signature(&n_pmcs);

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
  int select_all = cmd != cmd_collect || arg_count == 0;

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

  size_t i;
  struct stats_type *type;

  if (cmd == cmd_collect) {
    /* If arg_count is zero then we select all below. */
    for (i = 0; i < arg_count; i++) {
      type = stats_type_get(arg_list[i]);
      if (type == NULL) {
        ERROR("unknown type `%s'\n", arg_list[i]);
        continue;
      }
      type->st_selected = 1;
    }
  }

  auto_disable_optional_stats_by_lspci();

  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (enable_all)
      type->st_enabled = 1;

    if (!type->st_enabled)
      continue;

    if (stats_type_init(type) < 0) {
      type->st_enabled = 0;
      continue;
    }
    
    if (select_all)
      type->st_selected = 1;

    if (cmd == cmd_begin && type->st_begin != NULL)
      (*type->st_begin)(type);

    if (type->st_enabled && type->st_selected)
      (*type->st_collect)(type);
  }

  if (mark != NULL)
    stats_file_mark(&sf, "%s", mark);
  else if (cmd == cmd_begin || cmd == cmd_end)
    /* On begin set mark to "begin JOBID", and similar for end. */
    stats_file_mark(&sf, "%s %s", cmd_str, arg_count > 0 ? arg_list[0] : "-");

  if (stats_file_close(&sf) < 0)
    rc = 1;

  /* Cleanup. */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);

 out:
  return rc;
}
