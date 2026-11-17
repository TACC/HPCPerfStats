/* DEBUG-only latest full payload dump under /dev/shm. */
#ifndef STATS_BUFFER_DEBUG_SHM_H
#define STATS_BUFFER_DEBUG_SHM_H

#include <stddef.h>

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
/*! Atomically write `len` bytes as `<debug shm dir>/<name>` (mode 0600). */
void stats_buffer_debug_shm_write_file(const char *name, const void *data, size_t len);
void stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                          enum stats_buffer_debug_shm_payload_kind kind);
void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf, enum stats_row_tier tier);
/*! Begin a per-tick burst accumulator (shared-node: all jid + node-wide payloads). */
void stats_buffer_debug_shm_burst_begin(void);
/*! Append one outbound payload to the open burst (no-op if begin not called). */
void stats_buffer_debug_shm_burst_append(const struct stats_buffer *sf);
/*! Atomically write accumulated burst to fast.burst or full.burst; then clear. */
void stats_buffer_debug_shm_burst_commit(enum stats_row_tier tier);
#else
static inline void stats_buffer_debug_shm_init(void) {}

static inline void
stats_buffer_debug_shm_write_payload(const struct stats_buffer *sf,
                                     enum stats_buffer_debug_shm_payload_kind kind)
{
  (void)sf;
  (void)kind;
}

static inline void stats_buffer_debug_shm_write_sample(const struct stats_buffer *sf,
                                                       enum stats_row_tier tier)
{
  (void)sf;
  (void)tier;
}

static inline void stats_buffer_debug_shm_burst_begin(void) {}

static inline void stats_buffer_debug_shm_burst_append(const struct stats_buffer *sf)
{
  (void)sf;
}

static inline void stats_buffer_debug_shm_burst_commit(enum stats_row_tier tier)
{
  (void)tier;
}
#endif

#endif
