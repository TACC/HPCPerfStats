/* Blank-aware DCGM field converters for nvidia_gpu assign path. */
#include <limits.h>
#include <stddef.h>

#include "cpu_counter_metrics_dcgm_util.h"
#include "nvidia_gpu_dcgm_field.h"

static unsigned long long clamp_double_to_ull(double v)
{
  if (v <= 0.0)
    return 0ULL;
  if (v >= (double)ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long)(v + 0.5);
}

void nvidia_gpu_dcgm_field_watts(const dcgmFieldValue_v1 *v, double *out)
{
  double watts;

  if (out == NULL)
    return;
  *out = 0.0;
  if (v == NULL)
    return;
  if (v->fieldType == DCGM_FT_DOUBLE) {
    watts = v->value.dbl;
    if (dcgm_fp64_value_is_blank(watts))
      return;
    *out = watts;
    return;
  }
  if (v->fieldType == DCGM_FT_INT64) {
    if (dcgm_int64_value_is_blank(v->value.i64))
      return;
    *out = (double)v->value.i64;
  }
}

void nvidia_gpu_dcgm_field_u64(const dcgmFieldValue_v1 *v, uint64_t *out)
{
  if (out == NULL)
    return;
  *out = 0;
  if (v == NULL)
    return;
  if (v->fieldType == DCGM_FT_DOUBLE) {
    if (dcgm_fp64_value_is_blank(v->value.dbl))
      return;
    *out = (uint64_t)clamp_double_to_ull(v->value.dbl);
    return;
  }
  if (v->fieldType == DCGM_FT_INT64) {
    if (dcgm_int64_value_is_blank(v->value.i64) || v->value.i64 <= 0)
      return;
    *out = (uint64_t)v->value.i64;
  }
}

int nvidia_gpu_dcgm_field_apply_i64(int64_t v, int64_t *out)
{
  if (out == NULL)
    return -1;
  if (dcgm_int64_value_is_blank((long long)v))
    return -1;
  *out = v;
  return 0;
}

int nvidia_gpu_dcgm_field_ratio(double v, double *out)
{
  if (out == NULL)
    return -1;
  if (dcgm_fp64_value_is_blank(v))
    return -1;
  if (v >= 0.0 && v <= 1.0) {
    *out = v;
    return 0;
  }
  return -1;
}
