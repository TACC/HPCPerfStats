/*
 * Satisfy symbols referenced by exported-but-unused-in-test paths in stats_buffer.c
 * when linking a minimal test_ring_buffer binary.
 */
#include <stddef.h>

#include "dict.h"
#include "stats.h"

void cpu_stats_invalidate_file_caches(void) {}

void net_stats_invalidate_iface_cache(void) {}

int pscanf(const char *path, const char *fmt, ...)
{
  (void)path;
  (void)fmt;
  return 0;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  (void)i;
  return NULL;
}

char *dict_for_each(struct dict *dict, size_t *i)
{
  (void)dict;
  (void)i;
  return NULL;
}
