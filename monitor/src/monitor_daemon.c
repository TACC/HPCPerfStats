#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <math.h>
#include <limits.h>
#include <sys/fcntl.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/syslog.h>
#include <ev.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "fileio.h"
#include "string1.h"
#include "stats.h"
#include "collect.h"
#include "stats_buffer.h"
#include "metric_profiler.h"
#include "trace.h"
#include "pscanf.h"
#include "hwdetect.h"
#include "monitor_timing.h"
#include "monitor_log.h"
#include "monitor_options.h"
#include "stats_sink.h"
#include "stats_runtime.h"

char *app_name = NULL;
char *conf_file_name = NULL;
FILE *log_stream = NULL;
char *server = NULL;
char *queue  = (char *)monitor_cli_lit_queue;
char *port   = (char *)monitor_cli_lit_port;
char *rmq_user = (char *)monitor_cli_lit_rmq_user;
char *rmq_password = (char *)monitor_cli_lit_rmq_password;
char *dumpfile_dir = (char *)monitor_cli_lit_dumpfile_dir;
char *jobid_file_path = (char *)monitor_cli_lit_jobid_file_path;
double sample_freq = 300;
double send_freq = 300;
double buffer_hours = 6.0;
int max_buffer_size = 0;
int allow_ring_buffer_overwrite = 1;
int file_mode_enabled = 0;
int send_success_count = 0;
int send_success_count_max = 3;
ev_timer sample_timer;
ev_timer send_timer;
ev_timer rotate_timer;
static int max_buffer_size_explicit = 0;
static double sample_timer_period = 300.0;
static unsigned long g_payload_build_drop_count;
static unsigned long g_ring_insert_fail_count;
static unsigned long g_dumpfile_save_fail_count;

char jobid[80] = "-";
int nr_cpus;
int n_pmcs;
processor_t processor = (processor_t) 0;

static void send_dumpfile_stats(struct sf_ring_buffer *w);
static int save_file_stats_buffer(struct stats_buffer *sf);

/* Tier B: rate-limit repetitive operational logs on hot paths (ring/dumpfile resend). */
#ifdef DEBUG
#define MONITOR_HOT_LOG_EVERY 1u
#else
#define MONITOR_HOT_LOG_EVERY 32u
#endif

static void monitor_daemon_log_ring_resend_line(void)
{
  static unsigned seq;
  if (MONITOR_HOT_LOG_EVERY > 1u && (++seq % MONITOR_HOT_LOG_EVERY) != 1u)
    return;
  monitor_log_info( "Resending stats in the ring buffer\n");
}

static void monitor_daemon_log_dumpfile_resend_line(void)
{
  static unsigned seq;
  if (MONITOR_HOT_LOG_EVERY > 1u && (++seq % MONITOR_HOT_LOG_EVERY) != 1u)
    return;
  monitor_log_info( "Resending stats in the dumpfile\n");
}

static struct stats_buffer *monitor_daemon_alloc_stats_buffer(void)
{
  struct stats_buffer *sf = malloc(sizeof(*sf));
  if (sf == NULL) {
    ERROR("Failed allocating stats buffer\n");
    return NULL;
  }
  if (stats_buffer_open(sf, server, port, queue, rmq_user, rmq_password) < 0) {
    ERROR("Failed opening data buffer : %m\n");
    free(sf);
    return NULL;
  }
  return sf;
}

static int monitor_daemon_buffer_size_for_hours(double sfreq, double hours)
{
  double slots;
  if (sfreq <= 0.0)
    sfreq = 1.0;
  if (hours <= 0.0)
    hours = 1.0;
  slots = ceil((hours * 3600.0) / sfreq);
  if (slots < 1.0)
    slots = 1.0;
  if (slots > (double) INT_MAX)
    slots = (double) INT_MAX;
  return (int) slots;
}

static void monitor_daemon_apply_dynamic_buffer_size_if_needed(void)
{
  if (max_buffer_size_explicit)
    return;
  max_buffer_size = monitor_daemon_buffer_size_for_hours(sample_freq, buffer_hours);
}

/* After a send attempt: if the ring buffer still has entries, try to drain it over RMQ. */
static void monitor_daemon_resend_ring_buffer_if_nonempty(struct sf_ring_buffer *w)
{
  if (w->q_count <= 0)
    return;
  monitor_daemon_log_ring_resend_line();
  ring_buffer_resend(w);
  if (w->q_count > 0)
    send_success_count = 0;
}

static void monitor_daemon_maybe_send_dumpfiles_after_sample_timer(struct sf_ring_buffer *w)
{
  if (file_mode_enabled != 1 || w->q_count != 0)
    return;
  if (send_success_count < send_success_count_max)
    return;
  monitor_daemon_log_dumpfile_resend_line();
  send_dumpfile_stats(w);
}

void monitor_daemon_conf_set_buffer_max(int value)
{
	max_buffer_size = value;
	max_buffer_size_explicit = 1;
}

static void monitor_daemon_maybe_send_dumpfiles_after_jobid_cleared(struct sf_ring_buffer *w,
								    const char *new_jobid)
{
  if (file_mode_enabled != 1 || w->q_count != 0)
    return;
  if (strcmp(new_jobid, "-") != 0)
    return;
  if (send_success_count <= 0)
    return;
  monitor_daemon_log_dumpfile_resend_line();
  send_dumpfile_stats(w);
}

static int get_dumpfile_number(void);

static double monitor_daemon_get_realtime_now(void)
{
  struct timespec ts;
  if (clock_gettime(CLOCK_REALTIME, &ts) != 0)
    return 0.0;
  return (double)ts.tv_sec + ((double)ts.tv_nsec / 1000000000.0);
}

void monitor_daemon_reanchor_sample_timer(struct ev_loop *loop, double period)
{
  double now = monitor_daemon_get_realtime_now();
  double wait;

  sample_timer_period = monitor_timing_normalize_period(period);
  wait = monitor_timing_seconds_until_next_boundary(now, sample_timer_period);

  ev_timer_stop(loop, &sample_timer);
  ev_timer_set(&sample_timer, wait, 0.0);
  ev_timer_start(loop, &sample_timer);
}

void monitor_daemon_finalize_runtime_settings(void)
{
  if (sample_freq <= 0.0)
    sample_freq = 1.0;
  if (send_freq <= 0.0)
    send_freq = 1.0;
  if (buffer_hours <= 0.0)
    buffer_hours = 1.0;
  monitor_daemon_apply_dynamic_buffer_size_if_needed();
}

int read_conf_file(void)
{
  FILE *conf_file_fd = NULL;
  int ret = 0;

  if (conf_file_name == NULL) {
    monitor_daemon_finalize_runtime_settings();
    return 0;
  }

  conf_file_fd = file_fopen_read(conf_file_name);
  if (conf_file_fd == NULL) {
    monitor_log_info( "Can not open config file: %s, error: %s",
            conf_file_name, strerror(errno));
    return -1;
  }

  char *line_buf = NULL;
  size_t line_buf_size = 0;
  while (getline(&line_buf, &line_buf_size, conf_file_fd) >= 0) {
    char *line = line_buf;
    char *key;

    str_trim_inplace(line_buf);
    if (line_buf[0] == '\0')
      continue;
    key = strsep(&line, " :\t=");
    if (key == NULL || line == NULL)
      continue;
    str_trim_inplace(key);
    str_trim_inplace(line);
    if (key[0] == '\0')
      continue;
    monitor_options_apply_daemon_conf_kv(key, line);
  }
  if (line_buf)
    free(line_buf);
  fclose(conf_file_fd);
  monitor_daemon_finalize_runtime_settings();
  return ret;
}

void monitor_daemon_prime_file_mode_from_dumpdir(void)
{
  if (get_dumpfile_number() > 0) {
    file_mode_enabled = 1;
    send_success_count = 0;
  }
}

static int monitor_daemon_sink_finalize_stats_buffer(void *opaque)
{
	struct stats_buffer *sf = opaque;

	return stats_buffer_collect(sf);
}

static const struct stats_sink_ops monitor_daemon_stats_collect_sink = {
    .finalize = monitor_daemon_sink_finalize_stats_buffer,
};

static int send_stats_buffer(struct stats_buffer *sf)
{
	return stats_runtime_collect_cycle(monitor_log_get_stream(), sf,
					   &monitor_daemon_stats_collect_sink,
					   0);
}

static void monitor_daemon_collect_to_ring(struct sf_ring_buffer *w, int write_hdr, const char *mark_line)
{
  struct stats_buffer *sf = monitor_daemon_alloc_stats_buffer();
  int rc;
  if (sf == NULL)
    return;

  stats_runtime_teardown();
  stats_runtime_daemon_prepare_types();
  if (write_hdr)
    stats_wr_hdr(sf);
  if (mark_line != NULL)
    stats_buffer_mark(sf, "%s", mark_line);
  w->b_count++;
  stats_collect_on_changeover = write_hdr ? 1 : 0;
  w->status = send_stats_buffer(sf);
  stats_collect_on_changeover = 0;
  if (w->status < 0) {
    g_payload_build_drop_count++;
    ERROR("Failed building stats payload. Dropping sample (drops=%lu)\n",
          g_payload_build_drop_count);
    stats_buffer_close(sf);
    free(sf);
    return;
  }
  rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
  if (rc < 0) {
    g_ring_insert_fail_count++;
    ERROR("Failed adding stats to ring buffer (failures=%lu). Saving stats to dumpfile\n",
          g_ring_insert_fail_count);
    rc = save_file_stats_buffer(sf);
    stats_buffer_close(sf);
    free(sf);
    if (rc == 0) {
      w->f_count++;
      file_mode_enabled = 1;
      send_success_count = 0;
    } else {
      g_dumpfile_save_fail_count++;
      ERROR("Failed saving dropped payload to dumpfile (failures=%lu)\n",
            g_dumpfile_save_fail_count);
    }
  }
}

static int get_dumpfile_number(void)
{
  DIR *d;
  struct dirent *dir;
  int n_files = 0;
  d = opendir(dumpfile_dir);
  if (d) {
    while ((dir = readdir(d)) != NULL) {
      if (dir->d_type == DT_REG)
        n_files++;
    }
    closedir(d);
  }
  return n_files;
}

static char **get_dumpfile_list(void)
{
  DIR *d;
  struct dirent *dir;
  char **name_list = NULL;
  int n_files = get_dumpfile_number();
  int i = 0;

  d = opendir(dumpfile_dir);
  if (!d)
    return NULL;

  name_list = (char **)calloc((size_t)n_files, sizeof(char *));
  if (name_list == NULL) {
    closedir(d);
    return NULL;
  }

  while ((dir = readdir(d)) != NULL) {
    if (dir->d_type != DT_REG)
      continue;
    size_t path_len = strlen(dumpfile_dir) + 1 + strlen(dir->d_name) + 1;
    name_list[i] = (char *)malloc(path_len);
    if (name_list[i] == NULL) {
      for (int j = 0; j < i; j++)
        free(name_list[j]);
      free(name_list);
      closedir(d);
      return NULL;
    }
    snprintf(name_list[i], path_len, "%s/%s", dumpfile_dir, dir->d_name);
    i++;
  }
  closedir(d);
  return name_list;
}

static char *get_current_dumpfile(void)
{
  struct timeval tp;
  gettimeofday(&tp, NULL);
  time_t t = tp.tv_sec;
  struct tm *time_info = localtime(&t);
  char *time_str = (char *)malloc(sizeof(char) * 16);
  char *file_str;

  if (time_str == NULL)
    return NULL;
  strftime(time_str, 16, "%Y-%m-%d.sf", time_info);
  file_str = (char *)malloc(sizeof(char) * 64);
  if (file_str == NULL) {
    free(time_str);
    return NULL;
  }
  snprintf(file_str, sizeof(char) * 64, "%s/%s", dumpfile_dir, time_str);
  free(time_str);
  return file_str;
}

static int save_file_stats_buffer(struct stats_buffer *sf)
{
  int rc;
  char *file_path = get_current_dumpfile();

  if (file_path == NULL) {
    ERROR("Failed allocating dumpfile path\n");
    return -1;
  }
  rc = stats_buffer_write_file(sf, file_path);
  if (rc != 0)
    ERROR("Failed saving stats to dumpfile\n");
  free(file_path);
  return rc;
}

static int save_file_ring_buffer(struct sf_ring_buffer *w)
{
  int rc;
  char *file_path;
  struct sf_queue *sfq;

  /* Nothing buffered (common on SIGINT/SIGTERM after successful sends) — not an error. */
  if (w->q_count == 0)
    return 0;

  file_path = get_current_dumpfile();
  if (file_path == NULL) {
    ERROR("Failed allocating dumpfile path\n");
    return -1;
  }
  sfq = w->q_first;
  do {
    rc = stats_buffer_write_file(sfq->sf, file_path);
    if (rc == -1) {
      ERROR("Error saving stats to dumpfile %s\n", file_path);
      free(file_path);
      return -1;
    }
    w->f_count++;
    sfq = sfq->forward;
  } while (sfq != w->q_first);
  free(file_path);
  return 0;
}

static void send_dumpfile_stats(struct sf_ring_buffer *w)
{
  int rc;
  int n_files = get_dumpfile_number();
  if (n_files <= 0)
    return;
  char **file_list = get_dumpfile_list();
  if (file_list == NULL) {
    ERROR("Error listing dumpfiles in `%s'\n", dumpfile_dir);
    return;
  }
  int n_files_deleted = 0;
  for (int i = 0; i < n_files; i++) {
    FILE *f = file_fopen_read(file_list[i]);
    if (f == NULL) {
      monitor_log_info( "Error opening stats file %s\n", file_list[i]);
      send_success_count = 0;
      break;
    }
    rc = ring_buffer_load_file(f, w, server, port, queue, rmq_user, rmq_password,
                               max_buffer_size, allow_ring_buffer_overwrite);
    fclose(f);
    if (rc == 0) {
      remove(file_list[i]);
      n_files_deleted++;
      monitor_daemon_log_ring_resend_line();
      ring_buffer_resend(w);
      if (w->q_count != 0) {
#ifdef DEBUG
	monitor_log_info( "w_q_count = %d\n", w->q_count);
#endif
        send_success_count = 0;
        break;
      }
    } else {
      monitor_log_info( "Error loading stats file %s\n", file_list[i]);
      send_success_count = 0;
      break;
    }
  }
  if (n_files_deleted == n_files)
    file_mode_enabled = 0;
  for (int i = 0; i < n_files; i++)
    free(file_list[i]);
  free(file_list);
}

void monitor_daemon_replay_dumpfiles_if_present(struct sf_ring_buffer *w)
{
  send_dumpfile_stats(w);
}

static void print_buffer_status(struct sf_ring_buffer *w)
{
#ifdef DEBUG
  const unsigned status_every = 1u;
#else
  const unsigned status_every = 64u;
#endif
  static unsigned long status_tick;
  if (status_every > 1u && (status_tick++ % status_every) != 0u)
    return;

  /* One fprintf: fewer lock/syscall round-trips than seven separate prints. */
  monitor_log_info(
	  "status = %d, allow_overwrite = %d, file_mode = %d, #succ_send = %d/%d\n"
	  "#acc_processed = %d, #cur_buffered = %d/%d, #acc_succ_sent = %d, #acc_succ_resent = %d\n"
	  "#acc_deleted = %d, #acc_saved = %d, #acc_loaded = %d\n",
	  w->status, allow_ring_buffer_overwrite, file_mode_enabled, send_success_count,
	  send_success_count_max, w->b_count, w->q_count, max_buffer_size, w->s_count, w->r_count,
	  w->d_count, w->f_count, w->l_count);
}

void monitor_daemon_rotate_collect_flush(struct sf_ring_buffer *w)
{
  monitor_daemon_collect_to_ring(w, 1, NULL);
  /*
   * Schema/`$` payloads must reach RabbitMQ promptly — otherwise archives stay stale until the first
   * send_timer tick (send_freq can be hundreds of seconds) after daemon restart or periodic rotate.
   */
  monitor_daemon_resend_ring_buffer_if_nonempty(w);
}

void monitor_daemon_rotate_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents)
{
  (void)loop;
  (void)revents;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  monitor_daemon_rotate_collect_flush(w);
  print_buffer_status(w);
}

void monitor_daemon_sample_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents)
{
  (void)revents;
  /* jobid: refreshed on ev_stat (JOBID file) and at startup; avoids fopen/fclose each tick. */
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  monitor_daemon_collect_to_ring(w, 0, NULL);
  monitor_daemon_reanchor_sample_timer(loop, sample_timer_period);
  print_buffer_status(w);
}

void monitor_daemon_send_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents)
{
  (void)loop;
  (void)revents;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  int q_before = w->q_count;
  monitor_daemon_resend_ring_buffer_if_nonempty(w);
  if (w->q_count < q_before)
    send_success_count++;
  else if (w->q_count > 0)
    send_success_count = 0;
  monitor_daemon_maybe_send_dumpfiles_after_sample_timer(w);
  print_buffer_status(w);
}

void monitor_daemon_fd_cb(struct ev_loop *loop, ev_stat *w_, int revents)
{
  (void)loop;
  (void)revents;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  char new_jobid[80] = "-";
  pscanf(jobid_file_path, "%79s", new_jobid);

  const char *mark_line = NULL;
  int write_hdr = 0;
  if (strcmp(jobid, new_jobid) != 0) {
    if (strcmp(new_jobid, "-") != 0) {
      strcpy(jobid, new_jobid);
      monitor_log_info( "Loading jobid %s from %s\n", jobid, jobid_file_path);
      sample_timer_period = sample_freq;
      mark_line = strf("begin %s", jobid);
    } else {
      monitor_log_info( "Unloading jobid %s from %s\n", jobid, jobid_file_path);
      mark_line = strf("end %s", jobid);
      /*
       * Idle (`-`) must keep the same sampling cadence as a cold start with no job.
       * Historic code used 3600s here; that left q_count at 0 for up to an hour while send_timer
       * still ran, so archives looked “stalled” and broker traffic was only from other hosts.
       */
      sample_timer_period = sample_freq;
      write_hdr = 1;
    }
    monitor_daemon_reanchor_sample_timer(EV_DEFAULT, sample_timer_period);
    monitor_daemon_collect_to_ring(w, write_hdr, mark_line);
    if (mark_line != NULL)
      free((void *) mark_line);
    monitor_daemon_maybe_send_dumpfiles_after_jobid_cleared(w, new_jobid);
  }
  strcpy(jobid, new_jobid);
  print_buffer_status(w);
}

void monitor_daemon_signal_cb_int(struct ev_loop *loop, ev_signal *sig, int revents)
{
  (void)revents;
  size_t i = 0;
  struct stats_type *type;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)sig->data;
  monitor_daemon_resend_ring_buffer_if_nonempty(w);
  save_file_ring_buffer(w);
  print_buffer_status(w);
  stats_buffer_rmq_shutdown();
  cpu_stats_invalidate_file_caches();
  net_stats_invalidate_iface_cache();
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);
  monitor_log_info( "Stopping hpcperfstatsd\n");
  if (pid_fd != -1) {
    lockf(pid_fd, F_ULOCK, 0);
    close(pid_fd);
  }
  if (pid_file_name != NULL)
    unlink(pid_file_name);
  ev_break(loop, EVBREAK_ALL);
}

void monitor_daemon_signal_cb_hup(struct ev_loop *loop, ev_signal *sig, int revents)
{
  (void)loop;
  (void)revents;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)sig->data;
  monitor_log_info( "Reloading hpcperfstatsd config file %s\n", conf_file_name);
  stats_buffer_runtime_caches_reset();
  read_conf_file();
  monitor_daemon_prime_file_mode_from_dumpdir();
  monitor_daemon_resend_ring_buffer_if_nonempty(w);
  if (w->q_count == 0)
    monitor_daemon_replay_dumpfiles_if_present(w);
  else
    monitor_log_info(
	    "Skipping dumpfile replay on reload: ring buffer still has %d queued sample(s)\n",
	    w->q_count);
  sample_timer_period = sample_freq;
  send_timer.repeat = send_freq;
  monitor_daemon_reanchor_sample_timer(EV_DEFAULT, sample_timer_period);
  ev_timer_again(EV_DEFAULT, &send_timer);
  monitor_log_info( "Setting hpcperfstatsd sample frequency to %.1fs\n", sample_freq);
  monitor_log_info( "Setting hpcperfstatsd send frequency to %.1fs\n", send_freq);
  monitor_log_info( "Setting hpcperfstatsd buffer capacity to %d samples (%.2fh)\n",
          max_buffer_size, buffer_hours);
  send_success_count = 0;
  /*
   * Match daemon startup: emit `$`/schema refresh after reload so archives see updated types
   * without waiting for the periodic rotate timer.
   */
  monitor_daemon_rotate_collect_flush(w);
  print_buffer_status(w);
}
