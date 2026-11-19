#ifndef NVIDIA_GPU_DCGM_PROFILES_H_
#define NVIDIA_GPU_DCGM_PROFILES_H_

#include "nvidia_gpu.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Watch FieldGroupCreate attempt ids: 0=full 1=split-no-dfma 2=core 3=basic. split-no-dfma is
 * full minus DFMA (1015) and the integer pipe (1016), which A100 / DCGM 3.1.8 does not support
 * while IMMA/HMMA (1013/1014) are; core drops all of 1013-1016. */
#define NVIDIA_GPU_WATCH_PROFILE_NR 4
#define NVIDIA_GPU_DCGM_NSPLIT_NO_DFMA (NVIDIA_GPU_NFIELDS - 2)

/*
 * Fill order[0..n) with profile attempt ids. Sticky last_profile (0..NR-1) is tried
 * first when valid; remaining ids follow in ascending order. Returns n (== NR).
 */
int nvidia_gpu_watch_attempt_order(int order[NVIDIA_GPU_WATCH_PROFILE_NR], int last_profile);

/*
 * Resolve FieldGroupCreate field list for attempt id.
 * Returns 0 on success, -1 if attempt is out of range or an out pointer is NULL.
 */
int nvidia_gpu_watch_profile_select(int attempt, const unsigned short **fid_out,
                                    unsigned int *nf_out, const char **name_out);

/* Non-zero if field_id appears in the FieldGroupCreate list for attempt. */
int nvidia_gpu_watch_profile_has_field(int attempt, unsigned short field_id);

#ifdef __cplusplus
}
#endif

#endif /* NVIDIA_GPU_DCGM_PROFILES_H_ */
