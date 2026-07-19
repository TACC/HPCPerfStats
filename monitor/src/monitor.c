/* RabbitMQ daemon entry: libev timers, schema rotation, jobid watcher bootstrap. */
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
#include "monitor_log.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"
#include "stats_runtime.h"
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

  static struct ev_signal sigterm;
  sigterm.data = (void *)rb;
  ev_signal_init(&sigterm, monitor_daemon_signal_cb_int, SIGTERM);
  ev_signal_start(EV_DEFAULT, &sigterm);
}

static void monitor_rmq_io_tick_cb(struct ev_loop *loop, ev_timer *w, int revents)
{
  (void)loop;
  (void)w;
  (void)revents;
  stats_buffer_rmq_service_io();
}

static void monitor_load_initial_jobid(void)
{
  if (jobid_file_path == NULL)
    snprintf(jobid, sizeof jobid, "%s", "-");
  else if (pscanf(jobid_file_path, "%79s", jobid) < 1)
    snprintf(jobid, sizeof jobid, "%s", "-");
}

static void monitor_start_timers_and_jobid_watcher(struct sf_ring_buffer *rb)
{
  static ev_stat fd_watcher;
  static ev_timer rmq_io_timer;

  /*
   * Full `$`/banner + `!` schema (`stats_wr_hdr`): listend treats payloads whose body begins with
   * `$` as rotation/schema messages.
   * Startup emits `$` synchronously in main (after CPU topology init) before dumpfile replay so a
   * fresh daemon rotates listend `current` immediately; this timer only handles periodic rotation.
   */
  rotate_timer.data = (void *)rb;
  ev_timer_init(&rotate_timer, monitor_daemon_rotate_timer_cb, (double)MONITOR_DAEMON_SCHEMA_ROTATE_SEC,
		(double)MONITOR_DAEMON_SCHEMA_ROTATE_SEC);
  ev_timer_start(EV_DEFAULT, &rotate_timer);
  monitor_log_info("Setting hpcperfstatsd schema header rotation every %ds\n",
		   MONITOR_DAEMON_SCHEMA_ROTATE_SEC);

  fd_watcher.data = (void *)rb;
  ev_stat_init(&fd_watcher, monitor_daemon_fd_cb, jobid_file_path, EV_READ);
  ev_stat_start(EV_DEFAULT, &fd_watcher);
  monitor_log_info("Starting hpcperfstatsd watching fd %s\n", jobid_file_path);

  sample_timer.data = (void *)rb;
  ev_timer_init(&sample_timer, monitor_daemon_sample_timer_cb, 0.0, 0.0);
  monitor_daemon_reanchor_sample_timer(EV_DEFAULT, sample_freq);
  monitor_log_info("Setting hpcperfstatsd sample frequency to %.1fs (epoch-aligned)\n", sample_freq);

  send_timer.data = (void *)rb;
  /* First tick ASAP so queued samples drain soon after startup; repeat stays send_freq. */
  ev_timer_init(&send_timer, monitor_daemon_send_timer_cb, 0.0, send_freq);
  ev_timer_start(EV_DEFAULT, &send_timer);
  monitor_log_info("Setting hpcperfstatsd send frequency to %.1fs\n", send_freq);
  /* rabbitmq-c sends AMQP heartbeats from wait_frame_inner; long send_freq with a broker-capped
   * heartbeat (e.g. 60s) otherwise yields "missed heartbeats from client" disconnects. */
  ev_timer_init(&rmq_io_timer, monitor_rmq_io_tick_cb, 10.0, 10.0);
  ev_timer_start(EV_DEFAULT, &rmq_io_timer);
  monitor_log_info("RMQ connection I/O service interval 10.0s (AMQP heartbeats)\n");
  monitor_log_info("Setting hpcperfstatsd buffer capacity to %d samples (%.2fh)\n",
                   max_buffer_size, buffer_hours);

  monitor_load_initial_jobid();
}

static void monitor_require_server_or_exit(void)
{
  if (server == NULL) {
    monitor_log_info("Must specify a server to send data to with -s [--server] argument or conf file.\n");
    exit(0);
  }
  monitor_log_info("hpcperfstatsd data to server %s on port %s.\n", server, port);
}

static void monitor_log_optional_driver_probe(void)
{
  int has_nvidia_gpu = 0;
  int has_amd_gpu = 0;
  int has_intel_gpu = 0;
  int has_ib = 0;
  int has_opa = 0;
  int has_nvidia_devnode = 0;
  int has_dcgm_lib = 0;

  hwdetect_probe_optional_stack_presence(&has_nvidia_gpu, &has_amd_gpu, &has_intel_gpu,
                                         &has_ib, &has_opa);
  has_nvidia_devnode = (access("/dev/nvidia0", F_OK) == 0) ? 1 : 0;
  has_dcgm_lib = (access("/usr/lib64/libdcgm.so", F_OK) == 0
                   || access("/usr/lib64/libdcgm.so.4", F_OK) == 0
                   || access("/usr/lib/libdcgm.so", F_OK) == 0
                   || access("/usr/lib/libdcgm.so.4", F_OK) == 0) ? 1 : 0;

  monitor_log_info(
      "Driver/stack probe: nvidia_gpu=%s (devnode=%s, libdcgm=%s), amd_gpu=%s, "
      "intel_gpu=%s, infiniband=%s, opa=%s\n",
      has_nvidia_gpu ? "detected" : "not detected",
      has_nvidia_devnode ? "yes" : "no",
      has_dcgm_lib ? "yes" : "no",
      has_amd_gpu ? "detected" : "not detected",
      has_intel_gpu ? "detected" : "not detected",
      has_ib ? "detected" : "not detected",
      has_opa ? "detected" : "not detected");
}

int main(int argc, char *argv[])
{
  srand(1);
  int daemonmode = 0;

  app_name = argv[0];
  monitor_cli_parse_args(argc, argv, &daemonmode);

  log_stream = stderr;
  monitor_log_set_stream(log_stream);
  read_conf_file();
  monitor_daemon_finalize_runtime_settings();

  if (daemonmode) {
    if (pid_file_name == NULL)
      pid_file_name = strdup("/var/run/hpcperfstatsd.pid");
    daemonize();
  }

  monitor_log_info("Started %s\n", app_name);

  monitor_try_mk_dumpdir();
  stats_buffer_debug_shm_init();
  monitor_daemon_prime_file_mode_from_dumpdir();

  struct sf_ring_buffer ring_buffer;
  memset(&ring_buffer, 0, sizeof(ring_buffer));

  monitor_install_ev_handlers(&ring_buffer);
  monitor_require_server_or_exit();
  monitor_log_optional_driver_probe();

  nr_cpus = sysconf(_SC_NPROCESSORS_ONLN);
  processor = signature(&n_pmcs);
  if (stats_runtime_daemon_ensure_types() < 0) {
    monitor_log_error("Failed preparing daemon runtime types at startup\n");
    return EXIT_FAILURE;
  }

  /*
   * Empty ring: publish `$` before replay so listend recreates `current` on startup.
   * If replay ran first, backlog sits ahead of `$` in the ring and broker/RMQ ordering could delay
   * rotation until those publishes succeed (head-of-line blocking).
   */
  monitor_daemon_rotate_collect_flush(&ring_buffer);
  monitor_log_info(
      "Startup schema/`$` banner publish attempted (ring depth afterward=%d; nonzero=publish backlog)\n",
      ring_buffer.q_count);

  monitor_daemon_replay_dumpfiles_if_present(&ring_buffer);
  monitor_start_timers_and_jobid_watcher(&ring_buffer);

  ev_run(EV_DEFAULT, 0);

  monitor_log_info("Stopped %s\n", app_name);

  monitor_cli_free_heap();

  return EXIT_SUCCESS;
}
