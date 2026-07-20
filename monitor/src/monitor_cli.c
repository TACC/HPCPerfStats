/* RabbitMQ daemon CLI: shared literals, argv parsing, and heap cleanup. */
#include <stdlib.h>
#include <string.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"
#include "monitor_options.h"

const char monitor_cli_lit_queue[] = "default";
const char monitor_cli_lit_port[] = "5672";
const char monitor_cli_lit_rmq_user[] = "hpcperfstats";
const char monitor_cli_lit_rmq_password[] = "hpcperfstats";
const char monitor_cli_lit_dumpfile_dir[] = "/tmp/hpcperfstats";
const char monitor_cli_lit_jobid_file_path[] = "/var/run/stats_jobid";

void monitor_cli_heap_dup_setting(char **slot, const char *default_literal, const char *value)
{
  if (slot == NULL || default_literal == NULL || value == NULL)
    return;
  if (*slot != NULL && *slot != (char *)default_literal)
    free(*slot);
  *slot = strdup(value);
}

void monitor_cli_print_usage(FILE *stream)
{
  if (stream == NULL)
    return;
  monitor_options_print_daemon_usage(stream);
}

void monitor_cli_parse_args(int argc, char *argv[], int *daemonmode_out)
{
  if (daemonmode_out == NULL)
    return;
  monitor_options_parse_daemon_argv(argc, argv, daemonmode_out);
}

void monitor_cli_free_heap(void)
{
  free(conf_file_name);
  conf_file_name = NULL;

  free(pid_file_name);
  pid_file_name = NULL;

  free(server);
  server = NULL;

  if (queue != NULL && queue != (char *)monitor_cli_lit_queue)
    free(queue);
  queue = (char *)monitor_cli_lit_queue;

  if (port != NULL && port != (char *)monitor_cli_lit_port)
    free(port);
  port = (char *)monitor_cli_lit_port;

  if (rmq_user != NULL && rmq_user != (char *)monitor_cli_lit_rmq_user)
    free(rmq_user);
  rmq_user = (char *)monitor_cli_lit_rmq_user;

  if (rmq_password != NULL && rmq_password != (char *)monitor_cli_lit_rmq_password)
    free(rmq_password);
  rmq_password = (char *)monitor_cli_lit_rmq_password;

  if (dumpfile_dir != NULL && dumpfile_dir != (char *)monitor_cli_lit_dumpfile_dir)
    free(dumpfile_dir);
  dumpfile_dir = (char *)monitor_cli_lit_dumpfile_dir;

  if (jobid_file_path != NULL && jobid_file_path != (char *)monitor_cli_lit_jobid_file_path)
    free(jobid_file_path);
  jobid_file_path = (char *)monitor_cli_lit_jobid_file_path;
}
