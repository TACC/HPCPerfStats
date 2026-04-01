#include <errno.h>
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "daemonize.h"
#include "monitor_cli.h"
#include "monitor_daemon.h"

const char monitor_cli_lit_queue[] = "default";
const char monitor_cli_lit_port[] = "5672";
const char monitor_cli_lit_rmq_user[] = "hpcperfstats";
const char monitor_cli_lit_rmq_password[] = "hpcperfstats";
const char monitor_cli_lit_dumpfile_dir[] = "/tmp/hpcperfstats";
const char monitor_cli_lit_jobid_file_path[] = "/var/run/stats_jobid";

void monitor_cli_heap_dup_setting(char **slot, const char *default_literal,
				  const char *value)
{
  if (*slot != NULL && *slot != (char *)default_literal)
    free(*slot);
  *slot = strdup(value);
}

void monitor_cli_print_usage(FILE *stream)
{
  fprintf(stream,
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

void monitor_cli_parse_args(int argc, char *argv[], int *daemonmode_out)
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
      monitor_cli_heap_dup_setting(&queue, monitor_cli_lit_queue, optarg);
      break;
    case 'p':
      monitor_cli_heap_dup_setting(&port, monitor_cli_lit_port, optarg);
      break;
    case 't':
      monitor_cli_heap_dup_setting(&dumpfile_dir, monitor_cli_lit_dumpfile_dir,
				   optarg);
      break;
    case 'b':
      max_buffer_size = atoi(optarg);
      break;
    case 'h':
      monitor_cli_print_usage(stderr);
      exit(0);
    case '?':
      fprintf(stderr, "Try `%s --help' for more information.\n",
	      program_invocation_short_name);
      exit(1);
    }
  }
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
