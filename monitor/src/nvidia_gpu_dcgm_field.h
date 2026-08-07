#ifndef NVIDIA_GPU_DCGM_FIELD_H_
#define NVIDIA_GPU_DCGM_FIELD_H_

#include <stdint.h>

#include "dcgm_fields.h"
#include "dcgm_structs.h"

/* Blank-aware DCGM field normalize for nvidia_gpu assign path (blank → 0). */
void nvidia_gpu_dcgm_field_watts(const dcgmFieldValue_v1 *v, double *out);
void nvidia_gpu_dcgm_field_u64(const dcgmFieldValue_v1 *v, uint64_t *out);
/* Returns 0 if applied, -1 if blank/NULL (out unchanged). */
int nvidia_gpu_dcgm_field_apply_i64(int64_t v, int64_t *out);
/* Returns 0 if ratio in [0,1] applied, -1 if blank/out-of-range/NULL. */
int nvidia_gpu_dcgm_field_ratio(double v, double *out);

#endif
