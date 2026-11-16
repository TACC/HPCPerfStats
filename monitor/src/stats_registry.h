/* Table of all compiled-in stats_type objects, in iteration order. */
#ifndef STATS_REGISTRY_H
#define STATS_REGISTRY_H

#include <stddef.h>

struct stats_type;

/* Ordered by variable name, not st_name, so stats_type_get() scans linearly.
 * When adding a collector, extend src/Makefile.am TYPES and this table under the
 * same preprocessor guards (see configure.ac). */
extern struct stats_type *const stats_type_table[];
extern const size_t stats_type_nr;

#endif
