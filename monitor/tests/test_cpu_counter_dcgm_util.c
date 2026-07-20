#ifdef MONITOR_CPU_BACKEND_DCGM

#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics_dcgm_util.h"

static int cmp_int(const void *a, const void *b)
{
  int x = *(const int *) a;
  int y = *(const int *) b;

  if (x < y)
    return -1;
  if (x > y)
    return 1;
  return 0;
}

static void test_clamp_percent(void)
{
  assert(dcgm_clamp_percent(-1.0) == 0.0);
  assert(dcgm_clamp_percent(0.0) == 0.0);
  assert(dcgm_clamp_percent(50.5) == 50.5);
  assert(dcgm_clamp_percent(100.0) == 100.0);
  assert(dcgm_clamp_percent(150.0) == 100.0);
}

static void test_scale_util_if_fraction(void)
{
  struct dcgm_cpu_sample s;

  memset(&s, 0, sizeof(s));
  dcgm_cpu_scale_util_if_fraction(NULL);

  s.util_total = 0.0;
  dcgm_cpu_scale_util_if_fraction(&s);
  assert(s.util_total == 0.0);

  s.util_total = 0.5;
  s.util_user = 0.3;
  s.util_nice = 0.05;
  s.util_sys = 0.1;
  s.util_irq = 0.05;
  dcgm_cpu_scale_util_if_fraction(&s);
  assert(s.util_total == 50.0);
  assert(s.util_user == 30.0);

  s.util_total = 75.0;
  s.util_user = 40.0;
  dcgm_cpu_scale_util_if_fraction(&s);
  assert(s.util_total == 75.0);
}

static void test_sample_from_jiffy_diff(void)
{
  struct dcgm_cpu_jifs prev = {
    .u = 10, .nice = 0, .sys = 10, .idle = 80, .iow = 0,
    .irq = 0, .sft = 0, .stl = 0, .gu = 0, .gn = 0,
  };
  struct dcgm_cpu_jifs cur = {
    .u = 12, .nice = 0, .sys = 12, .idle = 80, .iow = 0,
    .irq = 0, .sft = 0, .stl = 0, .gu = 0, .gn = 0,
  };
  struct dcgm_cpu_sample s;

  memset(&s, 0, sizeof(s));
  dcgm_cpu_sample_from_jiffy_diff(&s, &cur, &prev);
  assert(s.util_total == 100.0);
  assert(s.util_user == 50.0);
  assert(s.util_sys == 50.0);
  assert(s.util_irq == 0.0);
  assert(s.util_nice == 0.0);

  dcgm_cpu_sample_from_jiffy_diff(NULL, &cur, &prev);
  dcgm_cpu_sample_from_jiffy_diff(&s, NULL, &prev);
  dcgm_cpu_sample_from_jiffy_diff(&s, &cur, NULL);

  cur.u = 1;
  cur.sys = 1;
  cur.idle = 50;
  memset(&s, 0, sizeof(s));
  dcgm_cpu_sample_from_jiffy_diff(&s, &cur, &prev);
  assert(s.util_total == 0.0);
}

static void test_count_unique_sorted_ints(void)
{
  int a[] = { 0, 0, 1, 1 };
  int b[] = { 7 };
  int c[] = { 3, 3, 3 };

  assert(dcgm_count_unique_sorted_ints(NULL, 4) == 0);
  assert(dcgm_count_unique_sorted_ints(a, 0) == 0);
  qsort(a, 4, sizeof(a[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(a, 4) == 2);
  qsort(b, 1, sizeof(b[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(b, 1) == 1);
  qsort(c, 3, sizeof(c[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(c, 3) == 1);
}

static void test_watts_dbl_to_ull(void)
{
  assert(dcgm_watts_dbl_to_ull(-1.0) == 0ULL);
  assert(dcgm_watts_dbl_to_ull(0.0) == 0ULL);
  assert(dcgm_watts_dbl_to_ull(50.4) == 50ULL);
  assert(dcgm_watts_dbl_to_ull(50.6) == 51ULL);
  /* DCGM_FP64_BLANK and blank-family must not emit as watts. */
  assert(dcgm_fp64_value_is_blank(140737488355328.0));
  assert(dcgm_fp64_value_is_blank(140737488355329.0));
  assert(!dcgm_fp64_value_is_blank(500.0));
  assert(dcgm_watts_dbl_to_ull(140737488355328.0) == 0ULL);
  assert(dcgm_watts_dbl_to_ull(140737488355330.0) == 0ULL);
}

int main(void)
{
  test_clamp_percent();
  test_scale_util_if_fraction();
  test_sample_from_jiffy_diff();
  test_count_unique_sorted_ints();
  test_watts_dbl_to_ull();
  printf("test_cpu_counter_dcgm_util passed\n");
  return 0;
}

#else

#include <stdio.h>

int main(void)
{
  printf("test_cpu_counter_dcgm_util skipped (not DCGM backend)\n");
  return 0;
}

#endif
