/* Test helpers for the direct-perf readers: a throwaway sysfs tree under TMPDIR and a fake
 * perf_event_open whose fds are pipes, so tests feed {value, enabled, running} samples. */
#ifndef TEST_FAKE_PERF_H_
#define TEST_FAKE_PERF_H_

#include <stdint.h>
#include <linux/perf_event.h>

#define FAKE_PERF_MAX 128

struct fake_perf_call {
  uint32_t type;
  uint64_t config;
  uint64_t read_format;
  int pinned;
  int exclusive;
  int cpu;
  int rfd; /* returned to the code under test (it closes it) */
  int wfd; /* kept by the test */
};

extern struct fake_perf_call g_fake_perf_calls[FAKE_PERF_MAX];
extern int g_fake_perf_n;

/** Creates a fresh tree root under TMPDIR into @p root (cap bytes). */
void fake_sysfs_init(char *root, unsigned long cap);
/** Writes @p content to <root>/<rel>, creating parent directories. */
void fake_sysfs_write(const char *root, const char *rel, const char *content);
/** Creates directory <root>/<rel> (and parents). */
void fake_sysfs_mkdir(const char *root, const char *rel);
/** Removes the whole tree. */
void fake_sysfs_rm(const char *root);

/** arm_perf_open_fn: records the call and returns a pipe read end, or fails (errno ENOENT) when
 * the attr config matches fake_perf_fail(). */
int fake_perf_open(const struct perf_event_attr *attr, int cpu);
/** Fail opens with @p config on @p cpu (-1 = every CPU). */
void fake_perf_fail(uint64_t config, int cpu);
/** Writes one {value, enabled, running} sample for the fd returned by call @p idx. */
void fake_perf_push(int idx, uint64_t value, uint64_t enabled, uint64_t running);
/** Index of the open call with @p config on @p cpu (and @p type when non-zero), or -1. */
int fake_perf_find(uint32_t type, uint64_t config, int cpu);
/** 1 when the code under test closed the fd of call @p idx (checked on the pipe itself, so a
 * reused fd number, e.g. syslog's socket, cannot fool it). */
int fake_perf_closed(int idx);
/** Closes the write ends and forgets every call and failure rule. */
void fake_perf_reset(void);

#endif
