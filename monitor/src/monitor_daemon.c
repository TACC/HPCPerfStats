#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
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
#include "stats_buffer.h"
#include "trace.h"
#include "pscanf.h"
#include "hwdetect.h"

char *app_name = NULL;
char *conf_file_name = NULL;
FILE *log_stream = NULL;
char *server = NULL;
char *queue  = (char *)monitor_cli_lit_queue;
char *port   = (char *)monitor_cli_lit_port;
char *rmq_user = (char *)monitor_cli_lit_rmq_user;
char *rmq_password = (char *)monitor_cli_lit_rmq_password;
char *dumpfile_dir = (char *)monitor_cli_lit_dumpfile_dir;
double freq = 300;
int max_buffer_size = 4096;
int allow_ring_buffer_overwrite = 1;
int file_mode_enabled = 0;
int send_success_count = 0;
int send_success_count_max = 3;
ev_timer sample_timer;
ev_timer rotate_timer;

char jobid[80] = "-";
int nr_cpus;
int n_pmcs;
processor_t processor = (processor_t) 0;

static void send_dumpfile_stats(struct sf_ring_buffer *w);
static int save_file_stats_buffer(struct stats_buffer *sf);

static void monitor_conf_strip_trailing_newline(char *s)
{
  size_t n;
  if (s == NULL || *s == '\0')
    return;
  n = strlen(s);
  if (n > 0 && s[n - 1] == '\n')
    s[n - 1] = '\0';
}

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
  fprintf(log_stream, "Resending stats in the ring buffer\n");
}

static void monitor_daemon_log_dumpfile_resend_line(void)
{
  static unsigned seq;
  if (MONITOR_HOT_LOG_EVERY > 1u && (++seq % MONITOR_HOT_LOG_EVERY) != 1u)
    return;
  fprintf(log_stream, "Resending stats in the dumpfile\n");
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

static void monitor_daemon_dispose_successful_send(struct sf_ring_buffer *w, struct stats_buffer *sf)
{
  w->s_count++;
  stats_buffer_close(sf);
  free(sf);
  if (file_mode_enabled == 1)
    send_success_count++;
}

/* Rotate timer: clear send_success_count before attempting ring insert. */
static void monitor_daemon_dispose_failed_send_rotate(struct sf_ring_buffer *w, struct stats_buffer *sf)
{
  ERROR("Failed sending stats. Adding stats to ring buffer\n");
  send_success_count = 0;
  int rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
  if (rc < 0) {
    ERROR("Failed adding stats to ring buffer. Saving stats to dumpfile\n");
    rc = save_file_stats_buffer(sf);
    stats_buffer_close(sf);
    free(sf);
    if (rc == 0) {
      w->f_count++;
      file_mode_enabled = 1;
    }
  }
}

/* Sample timer: clear send_success_count after ring insert (matches historical ordering). */
static void monitor_daemon_dispose_failed_send_sample(struct sf_ring_buffer *w, struct stats_buffer *sf)
{
  ERROR("Failed sending stats. Adding stats to ring buffer\n");
  int rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
  send_success_count = 0;
  if (rc < 0) {
    ERROR("Failed adding stats to ring buffer. Saving stats to dumpfile\n");
    rc = save_file_stats_buffer(sf);
    stats_buffer_close(sf);
    free(sf);
    if (rc == 0) {
      w->f_count++;
      file_mode_enabled = 1;
    }
  }
}

static void monitor_reset_all_stats_types(void)
{
  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);
}

static void monitor_init_enabled_stats_types(void)
{
  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL)
    type->st_enabled = 1;
  auto_disable_optional_stats_by_lspci();
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;
    if (stats_type_init(type) < 0) {
      type->st_enabled = 0;
      continue;
    }
    if (type->st_begin != NULL)
      (*type->st_begin)(type);
  }
}

static int get_dumpfile_number(void);

int read_conf_file(void)
{
  FILE *conf_file_fd = NULL;
  int ret = 0;

  if (conf_file_name == NULL)
    return 0;

  conf_file_fd = file_fopen_read(conf_file_name);
  if (conf_file_fd == NULL) {
    fprintf(log_stream, "Can not open config file: %s, error: %s",
            conf_file_name, strerror(errno));
    return -1;
  }

  char *line_buf = NULL;
  size_t line_buf_size = 0;
  while (getline(&line_buf, &line_buf_size, conf_file_fd) >= 0) {
    char *line = line_buf;
    char *key = strsep(&line, " :\t=");
    if (key == NULL || line == NULL)
      continue;
    while (*line == ' ')
      line++;
    monitor_conf_strip_trailing_newline(line);
    if (strcmp(key, "server") == 0) {
      free(server);
      server = strdup(line);
      fprintf(log_stream, "%s: Setting server to %s based on file %s\n",
              app_name, server, conf_file_name);
    }
    if (strcmp(key, "queue") == 0) {
      monitor_cli_heap_dup_setting(&queue, monitor_cli_lit_queue, line);
      fprintf(log_stream, "%s: Setting queue to %s based on file %s\n",
              app_name, queue, conf_file_name);
    }
    if (strcmp(key, "port") == 0) {
      monitor_cli_heap_dup_setting(&port, monitor_cli_lit_port, line);
      fprintf(log_stream, "%s: Setting server port to %s based on file %s\n",
              app_name, port, conf_file_name);
    }
    if (strcmp(key, "user") == 0) {
      monitor_cli_heap_dup_setting(&rmq_user, monitor_cli_lit_rmq_user, line);
      fprintf(log_stream, "%s: Setting RMQ user to %s based on file %s\n",
              app_name, rmq_user, conf_file_name);
    }
    if (strcmp(key, "password") == 0) {
      monitor_cli_heap_dup_setting(&rmq_password, monitor_cli_lit_rmq_password, line);
      fprintf(log_stream, "%s: Setting RMQ password from file %s\n",
              app_name, conf_file_name);
    }
    if (strcmp(key, "buffer") == 0) {
      max_buffer_size = atoi(line);
      fprintf(log_stream, "%s: Setting buffer size to %d based on file %s\n",
              app_name, max_buffer_size, conf_file_name);
    }
    if (strcmp(key, "freq") == 0) {
      if (sscanf(line, "%lf", &freq) == 1)
        fprintf(log_stream, "%s: Setting frequency to %f based on file %s\n",
                app_name, freq, conf_file_name);
    }
  }
  if (line_buf)
    free(line_buf);
  fclose(conf_file_fd);
  return ret;
}

void monitor_daemon_prime_file_mode_from_dumpdir(void)
{
  if (get_dumpfile_number() > 0) {
    file_mode_enabled = 1;
    send_success_count = 0;
  }
}

static int send_stats_buffer(struct stats_buffer *sf)
{
  size_t i = 0;
  struct stats_type *type;
  int rc = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (type->st_enabled)
      (*type->st_collect)(type);
  }
  if (stats_buffer_write(sf) < 0)
    rc = -1;
  return rc;
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
  strftime(time_str, 16, "%Y-%m-%d.sf", time_info);
  char *file_str = (char *)malloc(sizeof(char) * 64);
  snprintf(file_str, sizeof(char) * 64, "%s/%s", dumpfile_dir, time_str);
  free(time_str);
  return file_str;
}

static int save_file_stats_buffer(struct stats_buffer *sf)
{
  int rc;
  char *file_path = get_current_dumpfile();
  rc = stats_buffer_write_file(sf, file_path);
  if (rc != 0)
    ERROR("Failed saving stats to dumpfile\n");
  free(file_path);
  return rc;
}

static int save_file_ring_buffer(struct sf_ring_buffer *w)
{
  int rc = 0;
  char *file_path = NULL;
  struct sf_queue *sfq = NULL;
  if (w->q_count == 0) {
    rc = -1;
    goto err;
  }
  file_path = get_current_dumpfile();
  sfq = w->q_first;
  do {
    rc = stats_buffer_write_file(sfq->sf, file_path);
    if (rc == -1)
      goto err;
    w->f_count++;
    sfq = sfq->forward;
  } while (sfq != w->q_first);
err:
  if (rc != 0)
    ERROR("Error saving stats to dumpfile %s\n", file_path);
  free(file_path);
  return rc;
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
      fprintf(log_stream, "Error opening stats file %s\n", file_list[i]);
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
	fprintf(log_stream, "w_q_count = %d\n", w->q_count);
#endif
        send_success_count = 0;
        break;
      }
    } else {
      fprintf(log_stream, "Error loading stats file %s\n", file_list[i]);
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
  fprintf(log_stream,
	  "status = %d, allow_overwrite = %d, file_mode = %d, #succ_send = %d/%d\n"
	  "#acc_processed = %d, #cur_buffered = %d/%d, #acc_succ_sent = %d, #acc_succ_resent = %d\n"
	  "#acc_deleted = %d, #acc_saved = %d, #acc_loaded = %d\n",
	  w->status, allow_ring_buffer_overwrite, file_mode_enabled, send_success_count,
	  send_success_count_max, w->b_count, w->q_count, max_buffer_size, w->s_count, w->r_count,
	  w->d_count, w->f_count, w->l_count);
}

void monitor_daemon_rotate_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents)
{
  (void)loop;
  (void)revents;
  pscanf(JOBID_FILE_PATH, "%79s", jobid);
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  struct stats_buffer *sf = monitor_daemon_alloc_stats_buffer();
  if (sf == NULL)
    return;

  monitor_reset_all_stats_types();
  monitor_init_enabled_stats_types();
  stats_wr_hdr(sf);
  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0)
    monitor_daemon_dispose_successful_send(w, sf);
  else
    monitor_daemon_dispose_failed_send_rotate(w, sf);
  monitor_daemon_resend_ring_buffer_if_nonempty(w);
  print_buffer_status(w);
}

void monitor_daemon_sample_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents)
{
  (void)loop;
  (void)revents;
  /* jobid: refreshed on ev_stat (JOBID file) and at startup; avoids fopen/fclose each tick. */
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  struct stats_buffer *sf = monitor_daemon_alloc_stats_buffer();
  if (sf == NULL)
    return;

  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0)
    monitor_daemon_dispose_successful_send(w, sf);
  else
    monitor_daemon_dispose_failed_send_sample(w, sf);

  monitor_daemon_resend_ring_buffer_if_nonempty(w);
  monitor_daemon_maybe_send_dumpfiles_after_sample_timer(w);
  print_buffer_status(w);
}

void monitor_daemon_fd_cb(struct ev_loop *loop, ev_stat *w_, int revents)
{
  (void)loop;
  (void)revents;
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;
  char new_jobid[80] = "-";
  pscanf(JOBID_FILE_PATH, "%79s", new_jobid);

  struct stats_buffer *sf = monitor_daemon_alloc_stats_buffer();
  if (sf == NULL) {
    strcpy(jobid, new_jobid);
    return;
  }

  if (strcmp(jobid, new_jobid) != 0) {
    if (strcmp(new_jobid, "-") != 0) {
      strcpy(jobid, new_jobid);
      fprintf(log_stream, "Loading jobid %s from %s\n", jobid, JOBID_FILE_PATH);
      stats_buffer_mark(sf, "begin %s", jobid);
      sample_timer.repeat = freq;
    } else {
      fprintf(log_stream, "Unloading jobid %s from %s\n", jobid, JOBID_FILE_PATH);
      stats_buffer_mark(sf, "end %s", jobid);
      sample_timer.repeat = 3600;
    }
    ev_timer_again(EV_DEFAULT, &sample_timer);
  }

  monitor_reset_all_stats_types();
  monitor_init_enabled_stats_types();

  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0) {
    monitor_daemon_dispose_successful_send(w, sf);
    if (w->q_count > 0) {
      monitor_daemon_log_ring_resend_line();
      ring_buffer_resend(w);
      if (w->q_count != 0)
        send_success_count = 0;
    }
    monitor_daemon_maybe_send_dumpfiles_after_jobid_cleared(w, new_jobid);
  } else {
    ERROR("Failed sending stats. Adding stats to ring buffer\n");
    int rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
    if (rc < 0) {
      ERROR("Failed adding stats to ring buffer. Saving stats to file\n");
      rc = save_file_stats_buffer(sf);
      stats_buffer_close(sf);
      free(sf);
      if (rc == 0) {
        w->f_count++;
        file_mode_enabled = 1;
        send_success_count = 0;
      }
    }
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
  save_file_ring_buffer(w);
  print_buffer_status(w);
  stats_buffer_rmq_shutdown();
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);
  fprintf(log_stream, "Stopping hpcperfstatsd\n");
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
  fprintf(log_stream, "Reloading hpcperfstatsd config file %s\n", conf_file_name);
  stats_buffer_runtime_caches_reset();
  read_conf_file();
  sample_timer.repeat = freq;
  ev_timer_again(EV_DEFAULT, &sample_timer);
  send_success_count = 0;
  print_buffer_status(w);
}
