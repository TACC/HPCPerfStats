/* stats_buffer_debug_shm.h — DEBUG-only latest full payload dump under /dev/shm. */
#ifndef STATS_BUFFER_DEBUG_SHM_H
#define STATS_BUFFER_DEBUG_SHM_H

#include "stats_text_format.h"

struct stats_buffer;

enum stats_buffer_debug_shm_payload_kind {
  STATS_BUFFER_DEBUG_SHM_PAYLOAD_SCHEMA,
  STATS_BUFFER_DEBUG_SHM_PAYLOAD_FAST,
  STATS_BUFFER_DEBUG_SHM_PAYLOAD_FULL,
};

/* Schema/$ rotation payloads only. */
static inline int stats_buffer_debug_shm_schema_wanted(int write_hdr, int payload_ok)
{
  return payload_ok && write_hdr;
}

/* Routine samples only, not schema/$ hdr. */
static inline int stats_buffer_debug_shm_sample_wanted(int write_hdr, int payload_ok)
{
  return payload_ok && !write_hdr;
}

#ifdef DEBUG
void stats_buffer_debug_shm_init(void);
void stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                          enum stats_buffer_debug_shm_payload_kind kind);
void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
                                         enum stats_row_tier tier);
#else
static inline void stats_buffer_debug_shm_init(void)
{
}

static inline void stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                                        enum stats_buffer_debug_shm_payload_kind kind)
{
  (void) sf;
  (void) kind;
}

static inline void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
                                                       enum stats_row_tier tier)
{
  (void) sf;
  (void) tier;
}
#endif

#endif
