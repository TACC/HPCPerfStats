/*
 * RabbitMQ daemon CLI: shared literals, argv parsing, and heap cleanup.
 * Linked into hpcperfstatsd (RABBITMQ) and unit tests.
 */
#ifndef MONITOR_CLI_H
#define MONITOR_CLI_H

#include <stdio.h>

extern const char monitor_cli_lit_queue[];
extern const char monitor_cli_lit_port[];
extern const char monitor_cli_lit_rmq_user[];
extern const char monitor_cli_lit_rmq_password[];
extern const char monitor_cli_lit_dumpfile_dir[];

void monitor_cli_heap_dup_setting(char **slot, const char *default_literal,
				    const char *value);
void monitor_cli_print_usage(FILE *stream);
void monitor_cli_parse_args(int argc, char *argv[], int *daemonmode_out);
void monitor_cli_free_heap(void);

#endif
