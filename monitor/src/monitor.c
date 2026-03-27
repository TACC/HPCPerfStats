#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <ev.h>

#include "daemonize.h"
#include "monitor_daemon.h"
#include "string1.h"
#include "stats.h"
#include "trace.h"
#include "pscanf.h"
#include "hwdetect.h"

static const char *default_queue = "default";
static const char *default_port = "5672";
static const char *default_rmq_user = "hpcperfstats";
static const char *default_rmq_password = "hpcperfstats";
static const char *default_dumpfile_dir = "/tmp/hpcperfstats";

static void usage(void)
{
  fprintf(stderr,
          "Usage: %s [OPTION]... [TYPE]...\n"
          "Collect statistics.\n"
          "\n"
          "Mandatory arguments to long options are mandatory for short options too.\n"
          "  -h, --help         display this help and exit\n"
          "  -c [CONFIGFILE] or --configfile [CONFIGFILE] Configuration file to use.\n"
          "  -s [SERVER]     or --server     [SERVER]     Server to send data.\n"
          "  -q [QUEUE]      or --queue      [QUEUE]      Queue to route data to on RMQ server. \n"
          "  -p [PORT]       or --port       [PORT]       Port to use (5672 is the default).\n"
          "  -t [TMP_DIR]    or --tmp        [TMP_DIR]    Directory for dumpfiles (/tmp/hpcperfstats is the default).\n"
          "  -b [BUFFER]     or --buffer     [BUFFER]     Max size (in # of stats) for temporary in-memory storage (4096 is the default).\n"
          "  -f [FREQUENCY]  or --frequency  [FREQUENCY]  Frequency to sample (300 seconds is the default).\n",
          program_invocation_short_name);
}

static void monitor_replace_dup_string(char **slot, const char *default_literal, const char *value)
{
  if (*slot != NULL && *slot != (char *)default_literal)
    free(*slot);
  *slot = strdup(value);
}

static void monitor_parse_cli(int argc, char *argv[], int *daemonmode_out)
{
  struct option opts[] = {
    { "help",      no_argument, 0, 'h' },
    { "daemon",    no_argument, 0, 'd' },
    { "server",    required_argument, 0, 's' },
    { "queue",     required_argument, 0, 'q' },
    { "port",      required_argument, 0, 'p' },
    { "buffer",    required_argument, 0, 'b' },
    { "conf_file", required_argument, 0, 'c'},
    { "tmp_dir",   required_argument, 0, 't' },
    { "frequency", required_argument, 0, 'f' },
    { NULL, 0, 0, 0 },
  };

  *daemonmode_out = 0;

  int c;
  while ((c = getopt_long(argc, argv, "hdc:s:q:f:p:b:t:", opts, 0)) != -1) {
    switch (c) {
    case 'd':
      *daemonmode_out = 1;
      break;
    case 's':
      free(server);
      server = strdup(optarg);
      break;
    case 'f':
      freq = atof(optarg);
      break;
    case 'c':
      free(conf_file_name);
      conf_file_name = strdup(optarg);
      break;
    case 'q':
      monitor_replace_dup_string(&queue, default_queue, optarg);
      break;
    case 'p':
      monitor_replace_dup_string(&port, default_port, optarg);
      break;
    case 't':
      monitor_replace_dup_string(&dumpfile_dir, default_dumpfile_dir, optarg);
      break;
    case 'b':
      max_buffer_size = atoi(optarg);
      break;
    case 'h':
      usage();
      exit(0);
    case '?':
      fprintf(stderr, "Try `%s --help' for more information.\n", program_invocation_short_name);
      exit(1);
    }
  }
}

static void monitor_try_mk_dumpdir(void)
{
  if (mkdir(dumpfile_dir, 0777) < 0) {
    if (errno != EEXIST)
      ERROR("Cannot create directory %s\n", dumpfile_dir);
  }
}

static void monitor_install_ev_handlers(struct sf_ring_buffer *rb)
{
  signal(SIGPIPE, SIG_IGN);

  static struct ev_signal sigint;
  sigint.data = (void *)rb;
  ev_signal_init(&sigint, monitor_daemon_signal_cb_int, SIGINT);
  ev_signal_start(EV_DEFAULT, &sigint);

  static struct ev_signal sighup;
  sighup.data = (void *)rb;
  ev_signal_init(&sighup, monitor_daemon_signal_cb_hup, SIGHUP);
  ev_signal_start(EV_DEFAULT, &sighup);
}

static void monitor_start_timers_and_jobid_watcher(struct sf_ring_buffer *rb)
{
  ev_stat fd_watcher;

  rotate_timer.data = (void *)rb;
  ev_timer_init(&rotate_timer, monitor_daemon_rotate_timer_cb, 0.0, 86400);
  ev_timer_start(EV_DEFAULT, &rotate_timer);
  fprintf(log_stream, "Setting hpcperfstatsd rotate log files every %ds\n", 86400);

  fd_watcher.data = (void *)rb;
  ev_stat_init(&fd_watcher, monitor_daemon_fd_cb, JOBID_FILE_PATH, EV_READ);
  ev_stat_start(EV_DEFAULT, &fd_watcher);
  fprintf(log_stream, "Starting hpcperfstatsd watching fd %s\n", JOBID_FILE_PATH);

  sample_timer.data = (void *)rb;
  ev_timer_init(&sample_timer, monitor_daemon_sample_timer_cb, freq, freq);
  ev_timer_start(EV_DEFAULT, &sample_timer);
  fprintf(log_stream, "Setting hpcperfstatsd sample frequency to %.1fs\n", freq);
}

static void monitor_require_server_or_exit(void)
{
  if (server == NULL) {
    fprintf(log_stream, "Must specify a server to send data to with -s [--server] argument or conf file.\n");
    exit(0);
  }
  fprintf(log_stream, "hpcperfstatsd data to server %s on port %s.\n", server, port);
}

static void monitor_free_heap_options(void)
{
  if (conf_file_name != NULL)
    free(conf_file_name);
  if (pid_file_name != NULL)
    free(pid_file_name);
  if (server != NULL)
    free(server);
  if (queue != NULL && queue != (char *)default_queue)
    free(queue);
  if (port != NULL && port != (char *)default_port)
    free(port);
  if (rmq_user != NULL && rmq_user != (char *)default_rmq_user)
    free(rmq_user);
  if (rmq_password != NULL && rmq_password != (char *)default_rmq_password)
    free(rmq_password);
  if (dumpfile_dir != NULL && dumpfile_dir != (char *)default_dumpfile_dir)
    free(dumpfile_dir);
}

int main(int argc, char *argv[])
{
  srand(1);
  int daemonmode = 0;

  app_name = argv[0];
  monitor_parse_cli(argc, argv, &daemonmode);

  log_stream = stderr;
  read_conf_file();

  if (daemonmode) {
    if (pid_file_name == NULL)
      pid_file_name = strdup("/var/run/hpcperfstatsd.pid");
    daemonize();
  }

  fprintf(log_stream, "Started %s\n", app_name);

  monitor_try_mk_dumpdir();
  monitor_daemon_prime_file_mode_from_dumpdir();

  struct sf_ring_buffer ring_buffer;
  memset(&ring_buffer, 0, sizeof(ring_buffer));

  monitor_install_ev_handlers(&ring_buffer);
  monitor_require_server_or_exit();
  monitor_start_timers_and_jobid_watcher(&ring_buffer);

  nr_cpus = sysconf(_SC_NPROCESSORS_ONLN);
  processor = signature(&n_pmcs);

  ev_run(EV_DEFAULT, 0);

  fprintf(log_stream, "Stopped %s\n", app_name);

  monitor_free_heap_options();

  return EXIT_SUCCESS;
}
