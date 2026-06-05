#ifndef TEST_DEBUG_SHM_EMIT_VALIDATE_H_
#define TEST_DEBUG_SHM_EMIT_VALIDATE_H_

#include "stats_text_format.h"

/* Validate assembled sample payload shape: header, tier tokens, per-driver value
 * counts, and numeric values. `tier` is the payload row tier (FAST/FULL). */
int test_debug_shm_emit_validate_payload(const char *payload, size_t len,
				       enum stats_row_tier tier);

#endif
