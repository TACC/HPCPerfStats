/*
 * Minimal global state for test_monitor_cli (same layout as monitor_daemon.c).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <ev.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "stats_buffer.h"

int pid_fd;
char *pid_file_name;

char *app_name = NULL;
char *conf_file_name = NULL;
FILE *log_stream = NULL;
char *server = NULL;
char *queue = (char *)monitor_cli_lit_queue;
char *port = (char *)monitor_cli_lit_port;
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

void test_monitor_cli_reset_globals(void)
{
  free(conf_file_name);
  free(pid_file_name);
  free(server);
  if (queue != NULL && queue != (char *)monitor_cli_lit_queue)
    free(queue);
  if (port != NULL && port != (char *)monitor_cli_lit_port)
    free(port);
  if (rmq_user != NULL && rmq_user != (char *)monitor_cli_lit_rmq_user)
    free(rmq_user);
  if (rmq_password != NULL && rmq_password != (char *)monitor_cli_lit_rmq_password)
    free(rmq_password);
  if (dumpfile_dir != NULL && dumpfile_dir != (char *)monitor_cli_lit_dumpfile_dir)
    free(dumpfile_dir);

  app_name = NULL;
  conf_file_name = NULL;
  pid_file_name = NULL;
  pid_fd = 0;
  log_stream = NULL;
  server = NULL;
  queue = (char *)monitor_cli_lit_queue;
  port = (char *)monitor_cli_lit_port;
  rmq_user = (char *)monitor_cli_lit_rmq_user;
  rmq_password = (char *)monitor_cli_lit_rmq_password;
  dumpfile_dir = (char *)monitor_cli_lit_dumpfile_dir;
  freq = 300;
  max_buffer_size = 4096;
  allow_ring_buffer_overwrite = 1;
  file_mode_enabled = 0;
  send_success_count = 0;
  send_success_count_max = 3;
  memset(&sample_timer, 0, sizeof(sample_timer));
  memset(&rotate_timer, 0, sizeof(rotate_timer));
}
