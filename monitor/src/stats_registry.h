/* Lexicographically sorted table of all compiled-in stats_type objects. */
#ifndef STATS_REGISTRY_H
#define STATS_REGISTRY_H

#include <stddef.h>

struct stats_type;

/*
 * Order by stats_type.st_name — required for stats_type_get() binary search.
 * When adding a collector, extend src/Makefile.am TYPES and this table under the
 * same preprocessor guards (see configure.ac).
 */
extern struct stats_type *const stats_type_table[];
extern const size_t stats_type_nr;

#endif
