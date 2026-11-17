#ifndef NVIDIA_GPU_DCGM_FIELD_H_
#define NVIDIA_GPU_DCGM_FIELD_H_

#include <stdint.h>

#include "dcgm_fields.h"
#include "dcgm_structs.h"
#include "nvidia_gpu.h"
#include "stats_sentinel.h"

/* True for NVIDIA DCGM_FP64_BLANK and blank-family sentinels (>= blank). */
int dcgm_fp64_value_is_blank(double v);
/* True for NVIDIA DCGM_INT64_BLANK and blank-family sentinels (>= blank). */
int dcgm_int64_value_is_blank(long long v);

/* Blank-aware DCGM field converters: 0 when applied, -1 for a blank / negative / NULL value
 * (out set to 0). */
int nvidia_gpu_dcgm_field_watts(const dcgmFieldValue_v1 *v, double *out);
int nvidia_gpu_dcgm_field_u64(const dcgmFieldValue_v1 *v, uint64_t *out);
/* Returns 0 if applied, -1 if blank/NULL (out unchanged). */
int nvidia_gpu_dcgm_field_apply_i64(int64_t v, int64_t *out);
/* Returns 0 if ratio in [0,1] applied, -1 if blank/out-of-range/NULL. */
int nvidia_gpu_dcgm_field_ratio(double v, double *out);

/* nvidia_gpu schema key a DCGM field id fills, or NULL for fields with no direct key. */
const char *nvidia_gpu_dcgm_field_key(unsigned short field_id);
/* Value sentinel for a per-field DCGM status other than DCGM_ST_OK: NOT_SUPPORTED NOT_PRESENT,
 * anything else READ_FAILED. */
enum stats_sentinel nvidia_gpu_dcgm_status_sentinel(int status);
/* Record that `key` of this row was not read this collect (ignored for NULL / a full list);
 * nvidia_gpu publishes the list with stats_set_missing after the row's values. */
void nvidia_gpu_row_mark(dcgm_data_t *row, const char *key, enum stats_sentinel code);
/* Apply one DCGM field value to its row: a field status other than OK marks the key with
 * nvidia_gpu_dcgm_status_sentinel, a blank or out-of-range value marks it INVALID_VALUE. */
void nvidia_gpu_dcgm_field_apply(dcgm_data_t *row, const dcgmFieldValue_v1 *v);

#endif
