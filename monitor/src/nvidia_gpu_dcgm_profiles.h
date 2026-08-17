#ifndef NVIDIA_GPU_DCGM_PROFILES_H_
#define NVIDIA_GPU_DCGM_PROFILES_H_

#include "nvidia_gpu.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Watch FieldGroupCreate attempt ids: 0=full 1=core 2=basic 3=basic-no-board-power. */
#define NVIDIA_GPU_WATCH_PROFILE_NR 4

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
