/* monitor_options_apply_daemon_conf_kv: NULL, unknown, and empty-value paths. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <ev.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "monitor_log.h"
#include "monitor_options.h"

int pid_fd;
char *pid_file_name = NULL;
char *app_name = (char *)"test_monitor_options_kv";
char *conf_file_name = (char *)"/tmp/test.conf";
char *server = NULL;
char *queue = (char *)monitor_cli_lit_queue;
char *port = (char *)monitor_cli_lit_port;
char *rmq_user = (char *)monitor_cli_lit_rmq_user;
char *rmq_password = (char *)monitor_cli_lit_rmq_password;
char *dumpfile_dir = (char *)monitor_cli_lit_dumpfile_dir;
char *jobid_file_path = (char *)monitor_cli_lit_jobid_file_path;
double sample_freq = 30;
double sample_freq_slow = 600;
double send_freq = 300;
double buffer_hours = 6.0;
int enable_slow_tier = 1;
char *collection_profile = NULL;
char *disable_types = NULL;
int max_buffer_size = 0;
ev_timer sample_timer;
ev_timer send_timer;
ev_timer rotate_timer;

void monitor_daemon_conf_set_buffer_max(int value)
{
  max_buffer_size = value;
}

static void reset_defaults(void)
{
  if (queue != NULL && queue != (char *)monitor_cli_lit_queue)
    free(queue);
  if (port != NULL && port != (char *)monitor_cli_lit_port)
    free(port);
  queue = (char *)monitor_cli_lit_queue;
  port = (char *)monitor_cli_lit_port;
  max_buffer_size = 0;
  enable_slow_tier = 1;
  sample_freq = 30;
}

static void test_null_key_or_value_is_noop(void)
{
  const char *port_before = port;

  reset_defaults();
  monitor_options_apply_daemon_conf_kv(NULL, "5672");
  monitor_options_apply_daemon_conf_kv("port", NULL);
  assert(port == port_before);
}

static void test_unknown_key_is_noop(void)
{
  const char *port_before = port;

  reset_defaults();
  monitor_options_apply_daemon_conf_kv("not_a_real_key", "value");
  assert(port == port_before);
  assert(max_buffer_size == 0);
}

static void test_trimmed_key_values_apply(void)
{
  reset_defaults();
  monitor_options_apply_daemon_conf_kv("port", "9999");
  assert(port != NULL);
  assert(strcmp(port, "9999") == 0);

  reset_defaults();
  monitor_options_apply_daemon_conf_kv("buffer", "2048");
  assert(max_buffer_size == 2048);
}

static void test_empty_value_lines(void)
{
  reset_defaults();
  monitor_options_apply_daemon_conf_kv("enable_slow_tier", "");
  assert(enable_slow_tier == 0);

  reset_defaults();
  monitor_options_apply_daemon_conf_kv("sample_freq", "");
  assert(sample_freq == 30);
}

int main(void)
{
  test_null_key_or_value_is_noop();
  test_unknown_key_is_noop();
  test_trimmed_key_values_apply();
  test_empty_value_lines();
  printf("test_monitor_options_kv passed\n");
  return 0;
}
