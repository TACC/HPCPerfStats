#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "monitor_cli.h"
#include "monitor_daemon.h"

extern void test_monitor_cli_reset_globals(void);

static void next_cli_test(void)
{
  test_monitor_cli_reset_globals();
  optind = 1;
}

static void test_heap_dup_from_default_literal(void)
{
  char *slot = (char *)monitor_cli_lit_queue;
  monitor_cli_heap_dup_setting(&slot, monitor_cli_lit_queue, "q2");
  assert(slot != (char *)monitor_cli_lit_queue);
  assert(strcmp(slot, "q2") == 0);
  free(slot);
}

static void test_heap_dup_replaces_heap_value(void)
{
  char *slot = strdup("old");
  assert(slot != NULL);
  monitor_cli_heap_dup_setting(&slot, monitor_cli_lit_queue, "new");
  assert(strcmp(slot, "new") == 0);
  free(slot);
}

static void test_parse_sets_server(void)
{
  next_cli_test();
  char *argv[] = { "prog", "-s", "rmq.example", NULL };
  int dm = -1;
  monitor_cli_parse_args(3, argv, &dm);
  assert(dm == 0);
  assert(server != NULL && strcmp(server, "rmq.example") == 0);
}

static void test_parse_double_config_replaces(void)
{
  next_cli_test();
  char *argv[] = { "prog", "-c", "/tmp/a", "-c", "/tmp/b" };
  int dm;
  monitor_cli_parse_args(5, argv, &dm);
  assert(conf_file_name != NULL);
  assert(strcmp(conf_file_name, "/tmp/b") == 0);
}

static void test_parse_daemon_flag(void)
{
  next_cli_test();
  char *argv[] = { "prog", "-d" };
  int dm = 0;
  monitor_cli_parse_args(2, argv, &dm);
  assert(dm == 1);
}

static void test_help_invokes_usage_and_exits_zero(void)
{
  pid_t pid = fork();
  assert(pid >= 0);
  if (pid == 0) {
    test_monitor_cli_reset_globals();
    optind = 1;
    char *argv[] = { "prog", "-h" };
    int dm = 0;
    monitor_cli_parse_args(2, argv, &dm);
    _exit(99);
  }
  int st;
  assert(waitpid(pid, &st, 0) == pid);
  assert(WIFEXITED(st) && WEXITSTATUS(st) == 0);
}

static void test_free_heap_resets_queue_to_default(void)
{
  next_cli_test();
  char *argv[] = { "prog", "-q", "myqueue" };
  int dm;
  monitor_cli_parse_args(3, argv, &dm);
  assert(queue != (char *)monitor_cli_lit_queue);
  monitor_cli_free_heap();
  assert(queue == (char *)monitor_cli_lit_queue);
  assert(server == NULL);
  assert(conf_file_name == NULL);
}

int main(void)
{
  test_heap_dup_from_default_literal();
  test_heap_dup_replaces_heap_value();
  test_parse_sets_server();
  test_parse_double_config_replaces();
  test_parse_daemon_flag();
  test_help_invokes_usage_and_exits_zero();
  test_free_heap_resets_queue_to_default();
  test_monitor_cli_reset_globals();
  printf("test_monitor_cli passed\n");
  return 0;
}
