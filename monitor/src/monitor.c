#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <ev.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "string1.h"
#include "stats.h"
#include "trace.h"
#include "pscanf.h"
#include "hwdetect.h"

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

int main(int argc, char *argv[])
{
  srand(1);
  int daemonmode = 0;

  app_name = argv[0];
  monitor_cli_parse_args(argc, argv, &daemonmode);

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

  monitor_cli_free_heap();

  return EXIT_SUCCESS;
}
