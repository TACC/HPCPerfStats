/* Regression: /proc status field order — Uid/Vm* and Threads before affinity masks. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "host_key_alias.h"
#include "proc_status.h"
#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static void assert_stub_ull(const struct test_stats_stub *stub, const char *key,
                            unsigned long long expect)
{
  unsigned long long val = 0;

  assert(test_stats_stub_find(stub, key, &val));
  assert(val == expect);
}

static void test_pending_push_flush(void)
{
  struct proc_status_pending pend;
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  proc_status_pending_init(&pend);

  assert(proc_status_pending_push(NULL, "Uid", 1) == -1);
  assert(proc_status_pending_push(&pend, NULL, 1) == -1);
  assert(proc_status_pending_push(&pend, "NotAKey", 1) == -1);
  assert(pend.n == 0);

  assert(proc_status_pending_push(&pend, "Uid", 1000ULL) == 0);
  assert(proc_status_pending_push(&pend, "VmRSS", 4096ULL) == 0);
  assert(proc_status_pending_push(&pend, "Threads", 8ULL) == 0);
  assert(pend.n == 3);

  proc_status_pending_flush(&pend, &g_dummy_stats);
  assert(pend.n == 0);
  assert_stub_ull(&stub, "uid", 1000ULL);
  assert_stub_ull(&stub, "vm_rss", 4096ULL);
  assert_stub_ull(&stub, "threads", 8ULL);

  test_stats_stub_unbind();
}

static void test_pending_cap(void)
{
  struct proc_status_pending pend;
  unsigned i;

  proc_status_pending_init(&pend);
  for (i = 0; i < PROC_STATUS_PENDING_MAX; i++)
    assert(proc_status_pending_push(&pend, "Uid", (unsigned long long)i) == 0);
  assert(proc_status_pending_push(&pend, "VmRSS", 1ULL) == -1);
  assert(pend.n == PROC_STATUS_PENDING_MAX);
}

/*
 * Realistic Linux /proc/<pid>/status order: Name, then Uid/Vm* and Threads, then
 * Cpus_allowed_list / Mems_allowed_list. Stats row is created only after masks.
 */
static void test_realistic_status_order(void)
{
  struct proc_status_pending pend;
  struct stats *stats = NULL;
  struct test_stats_stub stub;
  unsigned long long val = 0;
  static const struct {
    const char *key;
    const char *rest;
  } lines[] = {
      {"Name:", "myjob"},
      {"State:", "R (running)"},
      {"Uid:", "1000\t1000\t1000\t1000"},
      {"VmPeak:", "123456 kB"},
      {"VmSize:", "120000 kB"},
      {"VmLck:", "0 kB"},
      {"VmHWM:", "8192 kB"},
      {"VmRSS:", "4096 kB"},
      {"VmData:", "2048 kB"},
      {"VmStk:", "136 kB"},
      {"VmExe:", "4 kB"},
      {"VmLib:", "3000 kB"},
      {"VmPTE:", "64 kB"},
      {"VmSwap:", "0 kB"},
      {"Threads:", "8"},
      {"Cpus_allowed_list:", "0-71"},
      {"Mems_allowed_list:", "0"},
  };
  size_t i;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  proc_status_pending_init(&pend);

  for (i = 0; i < sizeof(lines) / sizeof(lines[0]); i++) {
    if (strcmp(lines[i].key, "Mems_allowed_list:") == 0) {
      /* Identity complete: create row and flush deferred Uid/Vm* and Threads. */
      assert(stats == NULL);
      assert(pend.n > 0);
      stats = &g_dummy_stats;
      proc_status_pending_flush(&pend, stats);
    }
    proc_status_emit_or_defer_kv(stats, &pend, lines[i].key, lines[i].rest);
  }

  assert(stats != NULL);
  assert(pend.n == 0);
  assert_stub_ull(&stub, "uid", 1000ULL);
  assert_stub_ull(&stub, "vm_peak", 123456ULL);
  assert_stub_ull(&stub, "vm_size", 120000ULL);
  assert_stub_ull(&stub, "vm_rss", 4096ULL);
  assert_stub_ull(&stub, "vm_hwm", 8192ULL);
  assert_stub_ull(&stub, "threads", 8ULL);
  assert(test_stats_stub_find(&stub, "vm_data", &val));
  assert(val == 2048ULL);
  /* Affinity list keys are not aliased. */
  assert(!test_stats_stub_find(&stub, "cpus_allowed_list", &val));

  test_stats_stub_unbind();
}

static void test_emit_when_ready_no_defer(void)
{
  struct proc_status_pending pend;
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  proc_status_pending_init(&pend);

  proc_status_emit_or_defer_kv(&g_dummy_stats, &pend, "Uid:", "42");
  assert(pend.n == 0);
  assert_stub_ull(&stub, "uid", 42ULL);

  test_stats_stub_unbind();
}

int main(void)
{
  test_pending_push_flush();
  test_pending_cap();
  test_realistic_status_order();
  test_emit_when_ready_no_defer();
  printf("test_proc_status_order passed\n");
  return 0;
}
