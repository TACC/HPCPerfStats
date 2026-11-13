/* Test helpers: fake sysfs tree and pipe-backed fake perf_event_open (see test_fake_perf.h). */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "test_fake_perf.h"

struct fake_perf_call g_fake_perf_calls[FAKE_PERF_MAX];
int g_fake_perf_n;
static int g_fail_enabled;
static uint64_t g_fail_config;
static int g_fail_cpu;

void fake_sysfs_init(char *root, unsigned long cap)
{
  const char *tmp = getenv("TMPDIR");

  snprintf(root, cap, "%s/hps_fake_sysfs.XXXXXX", tmp != NULL ? tmp : "/tmp");
  assert(mkdtemp(root) != NULL);
}

static void mkdir_p(char *path)
{
  char *p;

  for (p = path + 1; *p != '\0'; p++) {
    if (*p != '/')
      continue;
    *p = '\0';
    assert(mkdir(path, 0700) == 0 || errno == EEXIST);
    *p = '/';
  }
  assert(mkdir(path, 0700) == 0 || errno == EEXIST);
}

void fake_sysfs_mkdir(const char *root, const char *rel)
{
  char path[1024];

  snprintf(path, sizeof(path), "%s/%s", root, rel);
  mkdir_p(path);
}

void fake_sysfs_write(const char *root, const char *rel, const char *content)
{
  char path[1024];
  char *slash;
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", root, rel);
  slash = strrchr(path, '/');
  *slash = '\0';
  mkdir_p(path);
  *slash = '/';
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(content, f);
  fclose(f);
}

void fake_sysfs_rm(const char *root)
{
  char cmd[1100];

  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
  assert(system(cmd) == 0);
}

int fake_perf_open(const struct perf_event_attr *attr, int cpu)
{
  struct fake_perf_call *c;
  int fds[2];

  if (g_fail_enabled && attr->config == g_fail_config && (g_fail_cpu < 0 || g_fail_cpu == cpu)) {
    errno = ENOENT;
    return -1;
  }
  assert(g_fake_perf_n < FAKE_PERF_MAX);
  assert(pipe(fds) == 0);
  /* Empty pipe = no sample this tick: read fails (EAGAIN) instead of blocking the test. */
  assert(fcntl(fds[0], F_SETFL, O_NONBLOCK) == 0);
  c = &g_fake_perf_calls[g_fake_perf_n++];
  c->type = attr->type;
  c->config = attr->config;
  c->read_format = attr->read_format;
  c->pinned = (int)attr->pinned;
  c->exclusive = (int)attr->exclusive;
  c->cpu = cpu;
  c->rfd = fds[0];
  c->wfd = fds[1];
  return fds[0];
}

void fake_perf_fail(uint64_t config, int cpu)
{
  g_fail_enabled = 1;
  g_fail_config = config;
  g_fail_cpu = cpu;
}

void fake_perf_push(int idx, uint64_t value, uint64_t enabled, uint64_t running)
{
  uint64_t rec[3] = {value, enabled, running};

  assert(idx >= 0 && idx < g_fake_perf_n);
  assert(write(g_fake_perf_calls[idx].wfd, rec, sizeof(rec)) == (ssize_t)sizeof(rec));
}

int fake_perf_find(uint32_t type, uint64_t config, int cpu)
{
  int i;

  for (i = 0; i < g_fake_perf_n; i++) {
    const struct fake_perf_call *c = &g_fake_perf_calls[i];

    if (c->config == config && c->cpu == cpu && (type == 0 || c->type == type))
      return i;
  }
  return -1;
}

int fake_perf_closed(int idx)
{
  struct pollfd p;

  assert(idx >= 0 && idx < g_fake_perf_n);
  p.fd = g_fake_perf_calls[idx].wfd;
  p.events = POLLOUT;
  p.revents = 0;
  /* The write end reports POLLERR once every read end of the pipe is closed. */
  return poll(&p, 1, 0) == 1 && (p.revents & POLLERR) != 0;
}

void fake_perf_reset(void)
{
  int i;

  for (i = 0; i < g_fake_perf_n; i++)
    close(g_fake_perf_calls[i].wfd);
  memset(g_fake_perf_calls, 0, sizeof(g_fake_perf_calls));
  g_fake_perf_n = 0;
  g_fail_enabled = 0;
}
