/* RabbitMQ daemon getopt_long parsing and hpcperfstats.conf key dispatch. */
#include <errno.h>
#include <getopt.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "monitor_log.h"
#include "monitor_options.h"

static int parse_double_arg(const char *raw, double *out)
{
  char *end = NULL;
  double v;

  if (raw == NULL || out == NULL || *raw == '\0')
    return -1;
  v = strtod(raw, &end);
  if (end == raw || *end != '\0' || !isfinite(v))
    return -1;
  *out = v;
  return 0;
}

void monitor_options_print_daemon_usage(FILE *stream)
{
  if (stream == NULL)
    return;
  fprintf(
      stream,
      "Usage: %s [OPTION]... [TYPE]...\n"
      "Collect statistics.\n"
      "\n"
      "Mandatory arguments to long options are mandatory for short options too.\n"
      "  -h, --help         display this help and exit\n"
      "  -c [CONFIGFILE] or --configfile [CONFIGFILE] or --config-file [CONFIGFILE] Configuration "
      "file to use.\n"
      "  -s [SERVER]     or --server     [SERVER]     Server to send data.\n"
      "  -q [QUEUE]      or --queue      [QUEUE]      Queue to route data to on RMQ server. \n"
      "  -p [PORT]       or --port       [PORT]       Port to use (5672 is the default).\n"
      "  -t [TMP_DIR]    or --tmp        [TMP_DIR]    Directory for dumpfiles (/tmp/hpcperfstats "
      "is the default).\n"
      "  -b [BUFFER]     or --buffer     [BUFFER]     Max size (in # of stats) for temporary "
      "in-memory storage (4096 is the default).\n"
      "  -f [FREQUENCY]  or --frequency  [FREQUENCY]  Deprecated alias for --sample-frequency.\n"
      "     [SECONDS]    or --sample-frequency [SECONDS] Sampling cadence in seconds (default "
      "30).\n"
      "     [SECONDS]    or --send-frequency   [SECONDS] RabbitMQ send cadence in seconds (default "
      "300).\n"
      "     [PROFILE]    or --collection-profile [PROFILE] Type profile: default|minimal|full.\n"
      "     [CSV]        or --disable-types [CSV] Comma-separated stats types to disable.\n",
      program_invocation_short_name);
}

void monitor_options_parse_daemon_argv(int argc, char *argv[], int *daemonmode_out)
{
  struct option opts[] = {
      {"help", no_argument, 0, 'h'},
      {"daemon", no_argument, 0, 'd'},
      {"server", required_argument, 0, 's'},
      {"queue", required_argument, 0, 'q'},
      {"port", required_argument, 0, 'p'},
      {"buffer", required_argument, 0, 'b'},
      {"conf_file", required_argument, 0, 'c'},
      {"configfile", required_argument, 0, 'c'},
      {"config-file", required_argument, 0, 'c'},
      {"tmp_dir", required_argument, 0, 't'},
      {"frequency", required_argument, 0, 'f'},
      {"sample-frequency", required_argument, 0, 'F'},
      {"send-frequency", required_argument, 0, 'S'},
      {"collection-profile", required_argument, 0, 'P'},
      {"disable-types", required_argument, 0, 'T'},
      {NULL, 0, 0, 0},
  };

  if (daemonmode_out == NULL)
    return;
  *daemonmode_out = 0;

  for (;;) {
    int c = getopt_long(argc, argv, "hdc:s:q:f:F:S:P:T:p:b:t:", opts, NULL);

    if (c == -1)
      break;

    switch (c) {
    case 'd':
      *daemonmode_out = 1;
      break;
    case 's':
      free(server);
      server = strdup(optarg);
      break;
    case 'f':
      if (parse_double_arg(optarg, &sample_freq) != 0)
        monitor_log_warn("%s: ignoring invalid --frequency value `%s`\n", app_name, optarg);
      break;
    case 'F':
      if (parse_double_arg(optarg, &sample_freq) != 0)
        monitor_log_warn("%s: ignoring invalid --sample-frequency value `%s`\n", app_name, optarg);
      break;
    case 'S':
      if (parse_double_arg(optarg, &send_freq) != 0)
        monitor_log_warn("%s: ignoring invalid --send-frequency value `%s`\n", app_name, optarg);
      break;
    case 'P':
      free(collection_profile);
      collection_profile = strdup(optarg);
      break;
    case 'T':
      free(disable_types);
      disable_types = strdup(optarg);
      break;
    case 'c':
      free(conf_file_name);
      conf_file_name = strdup(optarg);
      break;
    case 'q':
      monitor_cli_heap_dup_setting(&queue, monitor_cli_lit_queue, optarg);
      break;
    case 'p':
      monitor_cli_heap_dup_setting(&port, monitor_cli_lit_port, optarg);
      break;
    case 't':
      monitor_cli_heap_dup_setting(&dumpfile_dir, monitor_cli_lit_dumpfile_dir, optarg);
      break;
    case 'b':
      max_buffer_size = atoi(optarg);
      break;
    case 'h':
      monitor_options_print_daemon_usage(stderr);
      exit(0);
    case '?':
      fprintf(stderr, "Try `%s --help' for more information.\n", program_invocation_short_name);
      exit(1);
    }
  }
}

void monitor_options_apply_daemon_conf_kv(const char *key, const char *value_line)
{
  if (key == NULL || value_line == NULL)
    return;

  if (strcmp(key, "server") == 0) {
    free(server);
    server = strdup(value_line);
    monitor_log_info("%s: Setting server to %s based on file %s\n", app_name, server,
                     conf_file_name);
  }
  if (strcmp(key, "queue") == 0) {
    monitor_cli_heap_dup_setting(&queue, monitor_cli_lit_queue, value_line);
    monitor_log_info("%s: Setting queue to %s based on file %s\n", app_name, queue, conf_file_name);
  }
  if (strcmp(key, "port") == 0) {
    monitor_cli_heap_dup_setting(&port, monitor_cli_lit_port, value_line);
    monitor_log_info("%s: Setting server port to %s based on file %s\n", app_name, port,
                     conf_file_name);
  }
  if (strcmp(key, "user") == 0) {
    monitor_cli_heap_dup_setting(&rmq_user, monitor_cli_lit_rmq_user, value_line);
    monitor_log_info("%s: Setting RMQ user to %s based on file %s\n", app_name, rmq_user,
                     conf_file_name);
  }
  if (strcmp(key, "password") == 0) {
    monitor_cli_heap_dup_setting(&rmq_password, monitor_cli_lit_rmq_password, value_line);
    monitor_log_info("%s: Setting RMQ password from file %s\n", app_name, conf_file_name);
  }
  if (strcmp(key, "buffer") == 0) {
    monitor_daemon_conf_set_buffer_max(atoi(value_line));
    monitor_log_info("%s: Setting buffer size to %d based on file %s\n", app_name, max_buffer_size,
                     conf_file_name);
  }
  if (strcmp(key, "sample_freq") == 0) {
    if (parse_double_arg(value_line, &sample_freq) == 0)
      monitor_log_info("%s: Setting sample frequency to %f based on file %s\n", app_name,
                       sample_freq, conf_file_name);
    else
      monitor_log_warn("%s: ignoring invalid sample_freq `%s` in file %s\n", app_name, value_line,
                       conf_file_name);
  }
  if (strcmp(key, "sample_freq_slow") == 0) {
    if (parse_double_arg(value_line, &sample_freq_slow) == 0)
      monitor_log_info("%s: Setting slow sample frequency to %f based on file %s\n", app_name,
                       sample_freq_slow, conf_file_name);
    else
      monitor_log_warn("%s: ignoring invalid sample_freq_slow `%s` in file %s\n", app_name,
                       value_line, conf_file_name);
  }
  if (strcmp(key, "enable_slow_tier") == 0) {
    enable_slow_tier = (atoi(value_line) != 0) ? 1 : 0;
    monitor_log_info("%s: Setting enable_slow_tier to %d based on file %s\n", app_name,
                     enable_slow_tier, conf_file_name);
  }
  if (strcmp(key, "send_freq") == 0) {
    if (parse_double_arg(value_line, &send_freq) == 0)
      monitor_log_info("%s: Setting send frequency to %f based on file %s\n", app_name, send_freq,
                       conf_file_name);
    else
      monitor_log_warn("%s: ignoring invalid send_freq `%s` in file %s\n", app_name, value_line,
                       conf_file_name);
  }
  if (strcmp(key, "buffer_hours") == 0) {
    if (sscanf(value_line, "%lf", &buffer_hours) == 1)
      monitor_log_info("%s: Setting buffer hours to %f based on file %s\n", app_name, buffer_hours,
                       conf_file_name);
  }
  if (strcmp(key, "collection_profile") == 0) {
    free(collection_profile);
    collection_profile = strdup(value_line);
    monitor_log_info("%s: Setting collection profile to %s based on file %s\n", app_name,
                     collection_profile, conf_file_name);
  }
  if (strcmp(key, "disable_types") == 0) {
    free(disable_types);
    disable_types = strdup(value_line);
    monitor_log_info("%s: Setting disabled types to `%s` based on file %s\n", app_name,
                     disable_types, conf_file_name);
  }
  if (strcmp(key, "freq") == 0) {
    if (parse_double_arg(value_line, &sample_freq) == 0)
      monitor_log_info("%s: Deprecated key `freq` mapped to sample_freq=%f in file %s\n", app_name,
                       sample_freq, conf_file_name);
    else
      monitor_log_warn("%s: ignoring invalid deprecated freq `%s` in file %s\n", app_name,
                       value_line, conf_file_name);
  }
  if (strcmp(key, "jobid_file") == 0) {
    monitor_cli_heap_dup_setting(&jobid_file_path, monitor_cli_lit_jobid_file_path, value_line);
    monitor_log_info("%s: Setting jobid file to %s based on file %s\n", app_name, jobid_file_path,
                     conf_file_name);
  }
}
