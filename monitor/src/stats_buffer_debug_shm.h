/* stats_buffer_debug_shm.h — DEBUG-only latest sample dump under /dev/shm. */
#ifndef STATS_BUFFER_DEBUG_SHM_H
#define STATS_BUFFER_DEBUG_SHM_H

#include "stats_text_format.h"

struct stats_buffer;

#ifdef DEBUG
void stats_buffer_debug_shm_init(void);
void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
                                         enum stats_row_tier tier);
#else
static inline void stats_buffer_debug_shm_init(void)
{
}

static inline void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
                                                       enum stats_row_tier tier)
{
  (void) sf;
  (void) tier;
}
#endif

#endif
