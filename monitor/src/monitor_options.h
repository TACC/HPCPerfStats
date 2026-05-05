#ifndef MONITOR_OPTIONS_H
#define MONITOR_OPTIONS_H

#include <stdio.h>

void monitor_options_print_daemon_usage(FILE *stream);

/*! Parses RabbitMQ daemon argv (CLI flags consumed via getopt_long). */
void monitor_options_parse_daemon_argv(int argc, char *argv[], int *daemonmode_out);

/*! Dispatch one trimmed daemon configuration assignment (`value_line` is the RHS). */
void monitor_options_apply_daemon_conf_kv(const char *key, char *value_line);

#endif
