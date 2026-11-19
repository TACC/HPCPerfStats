/* Blank-aware nvidia_gpu DCGM field converters and per-field apply (no live libdcgm). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "nvidia_gpu_dcgm_compat.h"
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

/* The 15 PROF fields; core-prof drops the first four (1013-1016), basic-nonprof drops all. */
static const unsigned short g_prof_ids[] = {DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE,
                                            DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE,
                                            DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE,
                                            DCGM_FI_PROF_PIPE_INT_ACTIVE,
                                            DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
                                            DCGM_FI_PROF_PIPE_FP64_ACTIVE,
                                            DCGM_FI_PROF_PIPE_FP32_ACTIVE,
                                            DCGM_FI_PROF_PIPE_FP16_ACTIVE,
                                            DCGM_FI_PROF_SM_ACTIVE,
                                            DCGM_FI_PROF_SM_OCCUPANCY,
                                            DCGM_FI_PROF_DRAM_ACTIVE,
                                            DCGM_FI_PROF_PCIE_TX_BYTES,
                                            DCGM_FI_PROF_PCIE_RX_BYTES,
                                            DCGM_FI_PROF_NVLINK_TX_BYTES,
                                            DCGM_FI_PROF_NVLINK_RX_BYTES};
#define N_PROF (sizeof(g_prof_ids) / sizeof(g_prof_ids[0]))
static const unsigned short g_basic_ids[] = {DCGM_FI_DEV_POWER_USAGE,
                                             DCGM_FI_DEV_GPU_TEMP,
                                             DCGM_FI_DEV_MEM_COPY_UTIL,
                                             DCGM_FI_DEV_GPU_UTIL,
                                             DCGM_FI_DEV_FB_TOTAL,
                                             DCGM_FI_DEV_FB_USED,
                                             DCGM_FI_DEV_FB_FREE,
                                             DCGM_FI_DEV_SM_CLOCK,
                                             DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
                                             DCGM_FI_DEV_CLOCK_THROTTLE_REASONS};
#define N_BASIC (sizeof(g_basic_ids) / sizeof(g_basic_ids[0]))

/* Deliver one real value for field_id, as dcgmGetLatestValues does for a watched field. */
static void deliver(dcgm_data_t *row, unsigned short field_id)
{
  dcgmFieldValue_v1 v;

  if (field_id >= DCGM_FI_PROF_PCIE_TX_BYTES && field_id <= DCGM_FI_PROF_NVLINK_RX_BYTES)
    fill_i64(&v, 4096);
  else if (field_id >= DCGM_FI_PROF_GR_ENGINE_ACTIVE)
    fill_dbl(&v, 0.5);
  else if (field_id == DCGM_FI_DEV_POWER_USAGE)
    fill_dbl(&v, 300.0);
  else
    fill_i64(&v, 7);
  v.fieldId = field_id;
  nvidia_gpu_dcgm_field_apply(row, &v);
}

/* profile_absent: a field outside the active fallback watch profile never reaches the DCGM
 * callback, so its calloc 0 went out as a real 0. After the fetch every undelivered key is
 * NOT_PRESENT; delivered keys (OK or with their own status) are untouched. tensor_dmma_active has
 * no DCGM field at all, so it is NOT_PRESENT on every DCGM profile. */
static void test_profile_absent_fields_marked_not_present(void)
{
  dcgm_data_t row;
  dcgmFieldValue_v1 v;
  size_t i;

  /* core-prof: everything but tensor IMMA/HMMA/DFMA and the integer pipe. */
  memset(&row, 0, sizeof(row));
  for (i = 0; i < N_BASIC; i++)
    deliver(&row, g_basic_ids[i]);
  for (i = 4; i < N_PROF; i++)
    deliver(&row, g_prof_ids[i]);
  assert(row.nmiss == 0);
  nvidia_gpu_dcgm_row_mark_unseen(&row);
  assert(row.nmiss == 5);
  assert(miss_code(&row, "tensor_imma_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(miss_code(&row, "tensor_hmma_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(miss_code(&row, "tensor_dfma_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(miss_code(&row, "integer_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(miss_code(&row, "tensor_dmma_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(miss_code(&row, "tensor_active") == 0 && row.tensor_active == 0.5);

  /* basic-nonprof: every PROF key NOT_PRESENT; a basic field delivered with a failed status
   * keeps its own code and is not marked twice. */
  memset(&row, 0, sizeof(row));
  for (i = 1; i < N_BASIC; i++)
    deliver(&row, g_basic_ids[i]);
  fill_dbl(&v, 0.0);
  v.fieldId = DCGM_FI_DEV_POWER_USAGE;
  v.status = DCGM_ST_GENERIC_ERROR;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 1);
  nvidia_gpu_dcgm_row_mark_unseen(&row);
  assert(row.nmiss == 1 + (int)N_PROF + 1);
  assert(miss_code(&row, "power_usage") == STATS_SENTINEL_READ_FAILED);
  for (i = 0; i < N_PROF; i++)
    assert(miss_code(&row, nvidia_gpu_dcgm_field_key(g_prof_ids[i])) == STATS_SENTINEL_NOT_PRESENT);
  for (i = 1; i < N_BASIC; i++)
    assert(miss_code(&row, nvidia_gpu_dcgm_field_key(g_basic_ids[i])) == 0);

  /* full-prof: only the DMMA pipe (no DCGM field) is marked; NULL is ignored. */
  memset(&row, 0, sizeof(row));
  for (i = 0; i < N_BASIC; i++)
    deliver(&row, g_basic_ids[i]);
  for (i = 0; i < N_PROF; i++)
    deliver(&row, g_prof_ids[i]);
  nvidia_gpu_dcgm_row_mark_unseen(&row);
  assert(row.nmiss == 1 && miss_code(&row, "tensor_dmma_active") == STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_dcgm_row_mark_unseen(NULL);
}

/* integer_active: DCGM 1016 (integer pipe) fills its own key; 0.44-0.57 under FP loads on
 * Hopper, 0 on Blackwell. A blank is INVALID_VALUE, NOT_SUPPORTED (A100 DCGM 3.1.8) NOT_PRESENT. */
static void test_dcgm_integer_active_field(void)
{
  dcgm_data_t row;
  dcgmFieldValue_v1 v;

  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_PROF_PIPE_INT_ACTIVE), "integer_active") == 0);
  assert(DCGM_FI_PROF_PIPE_INT_ACTIVE == 1016);
  memset(&row, 0, sizeof(row));
  fill_dbl(&v, 0.44);
  v.fieldId = DCGM_FI_PROF_PIPE_INT_ACTIVE;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.nmiss == 0 && row.integer_active == 0.44);
  assert(row.tensor_dfma_active == 0.0 && row.fp64_active == 0.0);

  fill_dbl(&v, DCGM_TEST_FP64_BLANK);
  v.fieldId = DCGM_FI_PROF_PIPE_INT_ACTIVE;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(miss_code(&row, "integer_active") == STATS_SENTINEL_INVALID_VALUE);

  memset(&row, 0, sizeof(row));
  fill_dbl(&v, 0.5);
  v.fieldId = DCGM_FI_PROF_PIPE_INT_ACTIVE;
  v.status = DCGM_ST_NOT_SUPPORTED;
  nvidia_gpu_dcgm_field_apply(&row, &v);
  assert(row.integer_active == 0.0);
  assert(miss_code(&row, "integer_active") == STATS_SENTINEL_NOT_PRESENT);
}

int main(void)
{
  test_dcgm_integer_active_field();
  test_profile_absent_fields_marked_not_present();
  test_blank_sentinels();
  test_watts_blank_and_real();
  test_u64_blank_and_real();
  test_apply_i64_util_throttle();
  test_ratio_blank_and_bounds();
  test_apply_marks_blank_invalid();
  printf("test_nvidia_gpu_dcgm_field passed\n");
  return 0;
}
