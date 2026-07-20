/* daemonize(): double-fork and optional PID lock file (subprocess tests). */
#include <assert.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "daemonize.h"
#include "monitor_log.h"

static void test_daemonize_intermediate_child_exits_zero(void)
{
  pid_t pid = fork();

  assert(pid >= 0);
  if (pid == 0) {
    pid_file_name = NULL;
    daemonize();
    _exit(42);
  }

  {
    int st = -1;
    assert(waitpid(pid, &st, 0) == pid);
    assert(WIFEXITED(st) && WEXITSTATUS(st) == 0);
  }
}

static void test_daemonize_writes_pid_lock_file(void)
{
  char tmpl[] = "/tmp/hpc_daemonize_test.XXXXXX";
  int tmpfd = mkstemp(tmpl);
  pid_t grandchild = -1;
  pid_t pid;
  int st;

  assert(tmpfd >= 0);
  close(tmpfd);
  unlink(tmpl);

  pid = fork();
  assert(pid >= 0);
  if (pid == 0) {
    pid_file_name = strdup(tmpl);
    assert(pid_file_name != NULL);
    daemonize();
    _exit(0);
  }

  assert(waitpid(pid, &st, 0) == pid);
  assert(WIFEXITED(st) && WEXITSTATUS(st) == 0);

  {
    FILE *f = NULL;
    long gp = 0;
    int attempt;

    for (attempt = 0; attempt < 50 && f == NULL; attempt++) {
      usleep(20000);
      f = fopen(tmpl, "r");
    }
    assert(f != NULL);
    assert(fscanf(f, "%ld", &gp) == 1);
    fclose(f);
    assert(gp > 1);
    grandchild = (pid_t)gp;
  }

  kill(grandchild, SIGTERM);
  waitpid(grandchild, &st, WNOHANG);
  for (int attempt = 0; attempt < 50; attempt++) {
    if (waitpid(grandchild, &st, WNOHANG) == grandchild)
      break;
    usleep(20000);
  }
  unlink(tmpl);
  free(pid_file_name);
  pid_file_name = NULL;
}

int main(void)
{
  test_daemonize_intermediate_child_exits_zero();
  test_daemonize_writes_pid_lock_file();
  printf("test_daemonize passed\n");
  return 0;
}
