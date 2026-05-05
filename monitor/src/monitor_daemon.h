/*
 * Internal to the RabbitMQ daemon: shared between monitor.c (main, ev setup)
 * and monitor_daemon.c. Not an installable/public API.
 */
#ifndef MONITOR_DAEMON_H
#define MONITOR_DAEMON_H

#include <stdio.h>
#include <ev.h>
#include "stats_buffer.h"

extern char *app_name;
extern char *conf_file_name;
extern FILE *log_stream;
extern char *server;
extern char *queue;
extern char *port;
extern char *rmq_user;
extern char *rmq_password;
extern char *dumpfile_dir;
extern char *jobid_file_path;
extern double sample_freq;
extern double send_freq;
extern double buffer_hours;
extern int max_buffer_size;
extern int allow_ring_buffer_overwrite;
extern int file_mode_enabled;
extern int send_success_count;
extern int send_success_count_max;
extern ev_timer sample_timer;
extern ev_timer send_timer;
extern ev_timer rotate_timer;

int read_conf_file(void);
void monitor_daemon_conf_set_buffer_max(int value);
void monitor_daemon_finalize_runtime_settings(void);
void monitor_daemon_prime_file_mode_from_dumpdir(void);
void monitor_daemon_replay_dumpfiles_if_present(struct sf_ring_buffer *w);
void monitor_daemon_reanchor_sample_timer(struct ev_loop *loop, double period);

void monitor_daemon_rotate_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents);
void monitor_daemon_sample_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents);
void monitor_daemon_send_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents);
void monitor_daemon_fd_cb(struct ev_loop *loop, ev_stat *w_, int revents);
void monitor_daemon_signal_cb_int(struct ev_loop *loop, ev_signal *sig, int revents);
void monitor_daemon_signal_cb_hup(struct ev_loop *loop, ev_signal *sig, int revents);

#endif
