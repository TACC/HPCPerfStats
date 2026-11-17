/* Blank-aware nvidia_gpu DCGM field converters and per-field apply (no live libdcgm). */
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
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == -1 && out == 0.0);
  out = 99.0;
  fill_dbl(&v, DCGM_TEST_FP64_BLANK + 2.0);
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == -1 && out == 0.0);
  fill_dbl(&v, 300.5);
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == 0 && out == 300.5);
  fill_i64(&v, DCGM_TEST_INT64_BLANK);
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == -1 && out == 0.0);
  fill_i64(&v, 275);
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == 0 && out == 275.0);
  fill_dbl(&v, 0.0);
  assert(nvidia_gpu_dcgm_field_watts(&v, &out) == 0 && out == 0.0);
  assert(nvidia_gpu_dcgm_field_watts(NULL, &out) == -1 && out == 0.0);
}

static void test_u64_blank_and_real(void)
{
  dcgmFieldValue_v1 v;
  uint64_t out = 99ULL;

  fill_i64(&v, DCGM_TEST_INT64_BLANK);
  assert(nvidia_gpu_dcgm_field_u64(&v, &out) == -1 && out == 0ULL);
  fill_dbl(&v, DCGM_TEST_FP64_BLANK);
  assert(nvidia_gpu_dcgm_field_u64(&v, &out) == -1 && out == 0ULL);
  fill_i64(&v, 123456789LL);
  assert(nvidia_gpu_dcgm_field_u64(&v, &out) == 0 && out == 123456789ULL);
  fill_i64(&v, 0);
  assert(nvidia_gpu_dcgm_field_u64(&v, &out) == 0 && out == 0ULL);
  fill_i64(&v, -5LL);
  assert(nvidia_gpu_dcgm_field_u64(&v, &out) == -1 && out == 0ULL);
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

static void test_blank_sentinels(void)
{
  assert(dcgm_fp64_value_is_blank(140737488355328.0));
  assert(dcgm_fp64_value_is_blank(140737488355329.0));
  assert(!dcgm_fp64_value_is_blank(500.0));
  assert(dcgm_int64_value_is_blank(0x7ffffffffffffff0LL));
  assert(dcgm_int64_value_is_blank(0x7ffffffffffffff3LL));
  assert(!dcgm_int64_value_is_blank(0LL));
  assert(!dcgm_int64_value_is_blank(-1LL));
}

static unsigned int miss_code(const dcgm_data_t *row, const char *key)
{
  for (int i = 0; i < row->nmiss; i++)
    if (strcmp(row->miss[i].key, key) == 0)
      return row->miss[i].code;
  return 0;
}

/* nvidia_gpu_dcgm_field_apply: a blank value with status OK marks INVALID_VALUE on exactly its key,
 * a real 0 is applied with no mark, a bad field status keeps its status code. */
static void test_apply_marks_blank_invalid(void)
{
  dcgm_data_t row;
  dcgmFieldValue_v1 v;

  memset(&row, 0, sizeof(row));
  row.power_usage = 9.0;
  fill_dbl(&v, DCGM_TEST_FP64_BLANK);
  v.fieldId = DCGM_FI_DEV_POWER_USAGE;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 1 && miss_code(&row, "power_usage") == STATS_SENTINEL_INVALID_VALUE);

  fill_i64(&v, 0);
  v.fieldId = DCGM_FI_DEV_GPU_UTIL;
  row.gpu_util = 5;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 1 && row.gpu_util == 0 && miss_code(&row, "gpu_util") == 0);

  fill_i64(&v, DCGM_TEST_INT64_BLANK);
  v.fieldId = DCGM_FI_DEV_GPU_TEMP;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 2 && miss_code(&row, "temperature") == STATS_SENTINEL_INVALID_VALUE);

  fill_dbl(&v, 0.5);
  v.fieldId = DCGM_FI_PROF_SM_ACTIVE;
  v.status = DCGM_ST_NOT_SUPPORTED;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 3 && miss_code(&row, "sm_active") == STATS_SENTINEL_NOT_PRESENT);

  fill_dbl(&v, 0.0);
  v.fieldId = DCGM_FI_PROF_DRAM_ACTIVE;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 3 && row.dram_active == 0.0);
  nvidia_gpu_dcgm_field_apply(NULL, &v);
  nvidia_gpu_dcgm_field_apply(&row, NULL);
}

int main(void)
{
  test_blank_sentinels();
  test_watts_blank_and_real();
  test_u64_blank_and_real();
  test_apply_i64_util_throttle();
  test_ratio_blank_and_bounds();
  test_apply_marks_blank_invalid();
  printf("test_nvidia_gpu_dcgm_field passed\n");
  return 0;
}
