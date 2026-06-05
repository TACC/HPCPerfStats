#ifndef TEST_STATS_BUFFER_COLLECT_STUBS_H_
#define TEST_STATS_BUFFER_COLLECT_STUBS_H_

#include <stddef.h>

#include "stats.h"

struct stats_buffer_collect_fixture {
  struct stats_type type;
  struct stats *stats;
};

int stats_buffer_collect_fixture_init(struct stats_buffer_collect_fixture *fx,
				      const char *schema_def,
				      const unsigned long long *vals, size_t nvals);
void stats_buffer_collect_fixture_teardown(struct stats_buffer_collect_fixture *fx);

#endif
