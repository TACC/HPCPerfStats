/* Value sentinel catalog: the reasons one metric could not be read.
 *
 * A key whose state is not OK is emitted as the token "-<n>" instead of its
 * value, so an emitted 0 always means "read, and the counter was 0". Each entry
 * is X(n, SLUG, "description"); the wire code is -n.
 *
 * Codes are append-only: never renumber, reuse or remove an entry, and keep
 * n <= 255 (struct stats stores it in one unsigned char). The consumer mirror is
 * hpcperfstats/lib/monitor_sentinel.py; a drift test parses this header, so the
 * one-entry-per-line X(...) layout below is part of the contract. */
#ifndef _STATS_SENTINEL_H_
#define _STATS_SENTINEL_H_

#include <stddef.h>

/* Printed in the "$sentinels <version>" banner; bump when entries are appended. */
#define STATS_SENTINEL_CATALOG_VERSION 1

#define STATS_SENTINEL_CATALOG                                                                     \
  X(1, NOT_UPDATED, "not updated this cycle")                                                      \
  X(2, NOT_PRESENT, "counter not present")                                                         \
  X(3, READ_FAILED, "read failed")                                                                 \
  X(4, PERMISSION_DENIED, "permission denied")                                                     \
  X(5, PMU_BUSY, "PMU busy")                                                                       \
  X(6, NOT_SCHEDULED, "event not scheduled")                                                       \
  X(7, BACKEND_ERROR, "backend error")                                                             \
  X(8, INVALID_VALUE, "invalid value")                                                             \
  X(9, BACKOFF, "source in failure backoff")                                                       \
  X(10, NOT_BUILT, "not built into this monitor")                                                  \
  X(11, COUNTER_RESET, "counter reset")

enum stats_sentinel {
  STATS_SENTINEL_OK = 0,
#define X(n, slug, desc) STATS_SENTINEL_##slug = n,
  STATS_SENTINEL_CATALOG
#undef X
};

/* Number of catalog entries; codes are contiguous 1..STATS_SENTINEL_COUNT. */
enum {
#define X(n, slug, desc) +1
  STATS_SENTINEL_COUNT = 0 STATS_SENTINEL_CATALOG
#undef X
};

/* Slug for a catalog code, or NULL for 0 and for codes this build does not know. */
static inline const char *stats_sentinel_slug(unsigned int code)
{
  switch (code) {
#define X(n, slug, desc)                                                                           \
  case n:                                                                                          \
    return #slug;
    STATS_SENTINEL_CATALOG
#undef X
  default:
    return NULL;
  }
}

#endif
