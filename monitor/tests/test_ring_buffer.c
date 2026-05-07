/*
 * Ring buffer / stats_buffer integration tests (STATS_BUFFER_TEST_SEND_HOOK in stats_buffer.c).
 * Exercises ring_buffer_insert, ring_buffer_resend, ring_buffer_load_file without a live broker.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpuid.h"
#include "stats.h"
#include "stats_buffer.h"

char jobid[80] = "-";
double send_freq = 1.0;
int nr_cpus = 1;
int n_pmcs = 0;
processor_t processor = (processor_t)0;

static unsigned g_send_hook_calls;
/* When >= 0, return -1 from hook after this many successful invocations (for failure-path tests). */
static int g_fail_after_n = -1;

int stats_buffer_test_send_hook(struct stats_buffer *sf)
{
  (void)sf;
  g_send_hook_calls++;
  if (g_fail_after_n >= 0 && (int)g_send_hook_calls > g_fail_after_n)
    return -1;
  return 0;
}

static void reset_hook(void)
{
  g_send_hook_calls = 0;
  g_fail_after_n = -1;
}

static struct stats_buffer *make_payload_buf(const char *payload)
{
  struct stats_buffer *sf = (struct stats_buffer *)calloc(1, sizeof(*sf));
  assert(sf != NULL);
  assert(stats_buffer_open(sf, "127.0.0.1", "5672", "q", "u", "p") == 0);
  free(sf->sf_data);
  sf->sf_data = strdup(payload);
  assert(sf->sf_data != NULL);
  sf->sf_data_len = strlen(payload);
  sf->sf_data_cap = sf->sf_data_len + 1;
  return sf;
}

static void test_insert_empty_and_append(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_insert(make_payload_buf("a\n"), &w, 10, 1) == 0);
  assert(w.q_count == 1);
  assert(ring_buffer_insert(make_payload_buf("b\n"), &w, 10, 1) == 0);
  assert(w.q_count == 2);
}

static void test_insert_full_no_overwrite(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_insert(make_payload_buf("a\n"), &w, 1, 0) == 0);
  assert(ring_buffer_insert(make_payload_buf("b\n"), &w, 1, 0) == -1);
  assert(w.q_count == 1);
}

static void test_insert_full_overwrite(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_insert(make_payload_buf("a\n"), &w, 1, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("b\n"), &w, 1, 1) == 0);
  assert(w.q_count == 1);
}

static void test_insert_full_schema_protected(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_insert(make_payload_buf("$hdr\n"), &w, 1, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("new\n"), &w, 1, 1) == -1);
  assert(w.q_count == 1);
}

static void test_insert_full_prefers_non_schema_victim(void)
{
  struct sf_ring_buffer w;
  struct sf_queue *node;
  int saw_schema = 0;
  int saw_new = 0;
  int i;
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_insert(make_payload_buf("$hdr\n"), &w, 2, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("old\n"), &w, 2, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("new\n"), &w, 2, 1) == 0);
  node = w.q_first;
  for (i = 0; i < w.q_count; i++) {
    if (node->sf != NULL && node->sf->sf_data != NULL) {
      if (strstr(node->sf->sf_data, "$hdr") != NULL)
        saw_schema = 1;
      if (strstr(node->sf->sf_data, "new") != NULL)
        saw_new = 1;
    }
    node = node->forward;
  }
  assert(saw_schema == 1);
  assert(saw_new == 1);
}

static void test_resend_single_drains(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  reset_hook();
  assert(ring_buffer_insert(make_payload_buf("1.0 job host x\n"), &w, -1, 1) == 0);
  ring_buffer_resend(&w);
  assert(w.q_count == 0);
  assert(w.status == 0);
  assert(g_send_hook_calls == 1u);
}

static void test_resend_batch_merge(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  reset_hook();
  for (int i = 0; i < 3; i++)
    assert(ring_buffer_insert(make_payload_buf("1.0 job host x\n"), &w, -1, 1) == 0);
  ring_buffer_resend(&w);
  assert(w.q_count == 0);
  assert(w.status == 0);
  assert(g_send_hook_calls == 1u);
}

static void test_resend_schema_then_stats(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  reset_hook();
  assert(ring_buffer_insert(make_payload_buf(" $hdr\n"), &w, -1, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("1.0 job host x\n"), &w, -1, 1) == 0);
  ring_buffer_resend(&w);
  assert(w.q_count == 0);
  assert(g_send_hook_calls == 2u);
}

static void test_resend_stops_on_send_failure(void)
{
  struct sf_ring_buffer w;
  memset(&w, 0, sizeof(w));
  /* First payload succeeds; second schema send fails (hook fails after one good return). */
  reset_hook();
  g_fail_after_n = 1;
  assert(ring_buffer_insert(make_payload_buf("$hdr\n"), &w, -1, 1) == 0);
  assert(ring_buffer_insert(make_payload_buf("1.0 j h x\n"), &w, -1, 1) == 0);
  ring_buffer_resend(&w);
  assert(w.status != 0);
  assert(w.q_count == 1);
  reset_hook();
}

static void test_resend_limited_batches(void)
{
  struct sf_ring_buffer w;
  int processed = 0;

  memset(&w, 0, sizeof(w));
  reset_hook();
  assert(ring_buffer_insert(make_payload_buf("$hdr\n"), &w, -1, 1) == 0);
  for (int i = 0; i < 5; i++)
    assert(ring_buffer_insert(make_payload_buf("1.0 job host x\n"), &w, -1, 1) == 0);

  ring_buffer_resend_limited(&w, 1, -1, &processed);
  assert(processed > 0);
  assert(w.q_count > 0);
  assert(g_send_hook_calls == 1u);

  ring_buffer_resend(&w);
  assert(w.q_count == 0);
}

static void test_load_file_two_records(void)
{
  struct sf_ring_buffer w;
  FILE *f = tmpfile();
  assert(f != NULL);
  assert(fputs("line1\n", f) >= 0);
  assert(fputs("\n", f) >= 0);
  assert(fputs("line2\n", f) >= 0);
  rewind(f);
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_load_file(f, &w, "127.0.0.1", "5672", "q", "u", "p", -1, 1) == 0);
  /* Two records: line1 block, blank separator, line2 block; final insert flushes last. */
  assert(w.q_count == 2);
  assert(w.l_count == 2);
  fclose(f);
}

static void test_load_file_leading_blank_skipped(void)
{
  struct sf_ring_buffer w;
  FILE *f = tmpfile();
  assert(f != NULL);
  assert(fputs("\n\n", f) >= 0);
  assert(fputs("a\n", f) >= 0);
  /* No blank line after the record: only the final ring_buffer_insert flushes "a". */
  rewind(f);
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_load_file(f, &w, "127.0.0.1", "5672", "q", "u", "p", -1, 1) == 0);
  assert(w.q_count == 1);
  fclose(f);
}

static void test_load_file_open_fails(void)
{
  struct sf_ring_buffer w;
  FILE *f = tmpfile();
  memset(&w, 0, sizeof(w));
  assert(ring_buffer_load_file(f, &w, NULL, "5672", "q", "u", "p", -1, 1) == -1);
  fclose(f);
}

int main(void)
{
  test_insert_empty_and_append();
  test_insert_full_no_overwrite();
  test_insert_full_overwrite();
  test_insert_full_schema_protected();
  test_insert_full_prefers_non_schema_victim();
  test_resend_single_drains();
  test_resend_batch_merge();
  test_resend_schema_then_stats();
  test_resend_stops_on_send_failure();
  test_resend_limited_batches();
  test_load_file_two_records();
  test_load_file_leading_blank_skipped();
  test_load_file_open_fails();
  printf("test_ring_buffer passed\n");
  return 0;
}
