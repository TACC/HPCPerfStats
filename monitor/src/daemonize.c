/* Daemon double-fork, stdio redirect, and optional PID lock file. */
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "monitor_log.h"

int pid_fd;
char *pid_file_name;

#define DAEMON_PID_BUF 32

static void daemonize_exit_on_failure(void)
{
  exit(EXIT_FAILURE);
}

static void daemonize_first_fork(void)
{
  pid_t pid = fork();

  if (pid < 0)
    daemonize_exit_on_failure();
  if (pid > 0)
    exit(EXIT_SUCCESS);
}

static void daemonize_setup_session(void)
{
  if (setsid() < 0)
    daemonize_exit_on_failure();
  signal(SIGCHLD, SIG_IGN);
}

static void daemonize_close_fds(void)
{
  int fd;

  for (fd = (int)sysconf(_SC_OPEN_MAX); fd > 0; fd--)
    close(fd);
}

static int daemonize_open_devnull(int flags)
{
  int n = open("/dev/null", flags);

  if (n < 0)
    daemonize_exit_on_failure();
  return n;
}

static void daemonize_reopen_stdio(void)
{
  int n;

  n = daemonize_open_devnull(O_RDONLY);
  stdin = fdopen(n, "r");
  if (stdin == NULL)
    daemonize_exit_on_failure();

  n = daemonize_open_devnull(O_RDWR);
  stdout = fdopen(n, "w+");
  if (stdout == NULL)
    daemonize_exit_on_failure();

  n = daemonize_open_devnull(O_RDWR);
  stderr = fdopen(n, "w+");
  if (stderr == NULL)
    daemonize_exit_on_failure();
}

static void daemonize_write_pid_lock(void)
{
  char pidbuf[DAEMON_PID_BUF];
  int len;

  if (pid_file_name == NULL)
    return;

  monitor_log_info("%s\n", pid_file_name);
  pid_fd = open(pid_file_name, O_RDWR | O_CREAT, 0640);
  if (pid_fd < 0)
    daemonize_exit_on_failure();
  if (lockf(pid_fd, F_TLOCK, 0) < 0) {
    monitor_log_error("%s already found. Abort second instance.\n", pid_file_name);
    daemonize_exit_on_failure();
  }
  if (ftruncate(pid_fd, 0) != 0)
    daemonize_exit_on_failure();

  len = snprintf(pidbuf, sizeof(pidbuf), "%ld\n", (long)getpid());
  if (len < 0 || (size_t)len >= sizeof(pidbuf))
    daemonize_exit_on_failure();
  if (write(pid_fd, pidbuf, (size_t)len) != (ssize_t)len)
    daemonize_exit_on_failure();
}

void daemonize(void)
{
  daemonize_first_fork();
  daemonize_setup_session();
  daemonize_first_fork();

  umask(0);
  if (chdir("/") != 0)
    daemonize_exit_on_failure();

  daemonize_close_fds();
  daemonize_reopen_stdio();
  daemonize_write_pid_lock();
}
