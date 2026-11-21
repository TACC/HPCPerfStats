/* Recording stubs for stats_set_missing / stats_set_missing_all / stats_type_set_missing_all in
 * tests that link a collector without stats.c. Keys are recorded by name; the _all setters record
 * the key "*". */
#ifndef TEST_STATS_MISSING_STUB_H_
#define TEST_STATS_MISSING_STUB_H_

#include "stats.h"

#define TEST_STATS_MISSING_ALL_KEY "*"

void test_stats_missing_reset(void);
/* Last code recorded for `key` (TEST_STATS_MISSING_ALL_KEY for _all), or 0 when none. */
unsigned int test_stats_missing_code(const char *key);
/* Number of distinct keys recorded since the last reset. */
int test_stats_missing_count(void);

#endif
