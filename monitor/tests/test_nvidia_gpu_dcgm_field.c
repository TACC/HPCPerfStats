/* Blank-aware nvidia_gpu DCGM field converters (no live libdcgm). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "nvidia_gpu_dcgm_field.h"

#define DCGM_TEST_FP64_BLANK 140737488355328.0
#define DCGM_TEST_INT64_BLANK 0x7ffffffffffffff0LL

static void fill_dbl(dcgmFieldValue_v1 *v, double dbl)
{
  memset(v, 0, sizeof(*v));
  v->fieldType = DCGM_FT_DOUBLE;
  v->status = DCGM_ST_OK;
  v->value.dbl = dbl;
}

static void fill_i64(dcgmFieldValue_v1 *v, int64_t i64)
{
  memset(v, 0, sizeof(*v));
  v->fieldType = DCGM_FT_INT64;
  v->status = DCGM_ST_OK;
  v->value.i64 = i64;
}

static void test_watts_blank_and_real(void)
{
  dcgmFieldValue_v1 v;
  double out = 99.0;

  fill_dbl(&v, DCGM_TEST_FP64_BLANK);
  nvidia_gpu_dcgm_field_watts(&v, &out);
  assert(out == 0.0);

  out = 99.0;
  fill_dbl(&v, DCGM_TEST_FP64_BLANK + 2.0);
  nvidia_gpu_dcgm_field_watts(&v, &out);
  assert(out == 0.0);

  fill_dbl(&v, 300.5);
  nvidia_gpu_dcgm_field_watts(&v, &out);
  assert(out == 300.5);

  fill_i64(&v, DCGM_TEST_INT64_BLANK);
  nvidia_gpu_dcgm_field_watts(&v, &out);
  assert(out == 0.0);

  fill_i64(&v, 275);
  nvidia_gpu_dcgm_field_watts(&v, &out);
  assert(out == 275.0);

  nvidia_gpu_dcgm_field_watts(NULL, &out);
  assert(out == 0.0);
}

static void test_u64_blank_and_real(void)
{
  dcgmFieldValue_v1 v;
  uint64_t out = 99ULL;

  fill_i64(&v, DCGM_TEST_INT64_BLANK);
  nvidia_gpu_dcgm_field_u64(&v, &out);
  assert(out == 0ULL);

  fill_dbl(&v, DCGM_TEST_FP64_BLANK);
  nvidia_gpu_dcgm_field_u64(&v, &out);
  assert(out == 0ULL);

  fill_i64(&v, 123456789LL);
  nvidia_gpu_dcgm_field_u64(&v, &out);
  assert(out == 123456789ULL);

  fill_i64(&v, -5LL);
  nvidia_gpu_dcgm_field_u64(&v, &out);
  assert(out == 0ULL);
}

static void test_apply_i64_util_throttle(void)
{
  int64_t out = 42;

  assert(nvidia_gpu_dcgm_field_apply_i64(DCGM_TEST_INT64_BLANK, &out) == -1);
  assert(out == 42);

  assert(nvidia_gpu_dcgm_field_apply_i64(DCGM_TEST_INT64_BLANK + 3, &out) == -1);
  assert(out == 42);

  assert(nvidia_gpu_dcgm_field_apply_i64(87, &out) == 0);
  assert(out == 87);

  assert(nvidia_gpu_dcgm_field_apply_i64(0, &out) == 0);
  assert(out == 0);

  /* Valid throttle bit mask must pass; blank family must not. */
  assert(nvidia_gpu_dcgm_field_apply_i64(0x1LL, &out) == 0);
  assert(out == 0x1LL);
  assert(nvidia_gpu_dcgm_field_apply_i64(0, NULL) == -1);
}

static void test_ratio_blank_and_bounds(void)
{
  double out = 0.5;

  assert(nvidia_gpu_dcgm_field_ratio(DCGM_TEST_FP64_BLANK, &out) == -1);
  assert(out == 0.5);

  assert(nvidia_gpu_dcgm_field_ratio(0.25, &out) == 0);
  assert(out == 0.25);

  assert(nvidia_gpu_dcgm_field_ratio(1.5, &out) == -1);
  assert(out == 0.25);

  assert(nvidia_gpu_dcgm_field_ratio(-0.1, &out) == -1);
}

int main(void)
{
  test_watts_blank_and_real();
  test_u64_blank_and_real();
  test_apply_i64_util_throttle();
  test_ratio_blank_and_bounds();
  printf("test_nvidia_gpu_dcgm_field passed\n");
  return 0;
}
