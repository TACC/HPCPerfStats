//#include "daemonize.h"
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
#include <sys/stat.h>

#include "monitor_log.h"

int pid_fd;
char *pid_file_name;

void daemonize()
{
  pid_t pid = 0;
  int fd;

  /* Fork off the parent process */
  pid = fork();

  /* An error occurred */
  if (pid < 0) {
    exit(EXIT_FAILURE);
  }

  /* Success: Let the parent terminate */
  if (pid > 0) {
    exit(EXIT_SUCCESS);
  }

  /* On success: The child process becomes session leader */
  if (setsid() < 0) {
    exit(EXIT_FAILURE);
  }

  /* Ignore signal sent from child to parent process */
  signal(SIGCHLD, SIG_IGN);

  /* Fork off for the second time*/
  pid = fork();

  /* An error occurred */
  if (pid < 0) {
    exit(EXIT_FAILURE);
  }

  /* Success: Let the parent terminate */
  if (pid > 0) {
    exit(EXIT_SUCCESS);
  }

  /* Set new file permissions */
  umask(0);

  /* Change the working directory to the root directory */
  /* or another appropriated directory */
  if (chdir("/") != 0)
    exit(EXIT_FAILURE);

  /* Close all open file descriptors */
  for (fd = sysconf(_SC_OPEN_MAX); fd > 0; fd--) {
    close(fd);
  }
  /* Reopen stdin (fd = 0), stdout (fd = 1), stderr (fd = 2) */
  {
    int n;

    n = open("/dev/null", O_RDONLY);
    if (n < 0)
      exit(EXIT_FAILURE);
    stdin = fdopen(n, "r");
    if (stdin == NULL)
      exit(EXIT_FAILURE);
    n = open("/dev/null", O_RDWR);
    if (n < 0)
      exit(EXIT_FAILURE);
    stdout = fdopen(n, "w+");
    if (stdout == NULL)
      exit(EXIT_FAILURE);
    n = open("/dev/null", O_RDWR);
    if (n < 0)
      exit(EXIT_FAILURE);
    stderr = fdopen(n, "w+");
    if (stderr == NULL)
      exit(EXIT_FAILURE);
  }
  /* Try to write PID of daemon to lockfile */
  if (pid_file_name != NULL)
    {
      monitor_log_info("%s\n", pid_file_name);
      pid_fd = open(pid_file_name, O_RDWR|O_CREAT, 0640);
      if (pid_fd < 0) {
	/* Can't open lockfile */
	exit(EXIT_FAILURE);
      }
      if (lockf(pid_fd, F_TLOCK, 0) < 0) {
	/* Can't lock file */
	monitor_log_error("%s already found. Abort second instance.\n",
			  pid_file_name);
	exit(EXIT_FAILURE);
      }
      if (ftruncate(pid_fd, 0) != 0)
	exit(EXIT_FAILURE);
      {
	char pidbuf[32];
	int len = snprintf(pidbuf, sizeof(pidbuf), "%ld\n", (long)getpid());

	if (len < 0 || (size_t)len >= sizeof(pidbuf))
	  exit(EXIT_FAILURE);
	if (write(pid_fd, pidbuf, (size_t)len) != (ssize_t)len)
	  exit(EXIT_FAILURE);
      }
    }
}
