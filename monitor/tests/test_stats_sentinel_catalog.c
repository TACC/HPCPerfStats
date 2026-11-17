/* Contract for the value sentinel catalog (stats_sentinel.h): codes are
 * contiguous from 1, fit the one-byte state, and only ever grow by appending. */
#include <assert.h>
#include <string.h>

#include "stats_sentinel.h"

/* Released slugs in code order. Append new codes here; never edit or reorder. */
static const char *const golden_slugs[] = {
    "NOT_UPDATED", "NOT_PRESENT",   "READ_FAILED",   "PERMISSION_DENIED",
    "PMU_BUSY",    "NOT_SCHEDULED", "BACKEND_ERROR", "INVALID_VALUE",
    "BACKOFF",     "NOT_BUILT",     "COUNTER_RESET",
};

static void test_catalog_codes_contiguous_and_fit_state_byte(void)
{
  static const unsigned int codes[] = {
#define X(n, slug, desc) n,
      STATS_SENTINEL_CATALOG
#undef X
  };
  size_t n_codes = sizeof codes / sizeof codes[0];

  assert(n_codes == (size_t)STATS_SENTINEL_COUNT);
  assert(STATS_SENTINEL_COUNT <= 255);
  for (size_t i = 0; i < n_codes; i++)
    assert(codes[i] == i + 1);
}

static void test_catalog_released_slugs_append_only(void)
{
  size_t n_golden = sizeof golden_slugs / sizeof golden_slugs[0];

  assert((size_t)STATS_SENTINEL_COUNT >= n_golden);
  for (size_t i = 0; i < n_golden; i++) {
    const char *slug = stats_sentinel_slug((unsigned int)(i + 1));
    assert(slug != NULL);
    assert(strcmp(slug, golden_slugs[i]) == 0);
  }
}

static void test_catalog_enum_names_match_codes(void)
{
  assert(STATS_SENTINEL_OK == 0);
  assert(STATS_SENTINEL_NOT_UPDATED == 1);
  assert(STATS_SENTINEL_NOT_PRESENT == 2);
  assert(STATS_SENTINEL_COUNTER_RESET == 11);
}

static void test_catalog_slug_unknown_codes_null(void)
{
  assert(stats_sentinel_slug(STATS_SENTINEL_OK) == NULL);
  assert(stats_sentinel_slug(STATS_SENTINEL_COUNT + 1) == NULL);
  assert(stats_sentinel_slug(255) == NULL);
}

int main(void)
{
  test_catalog_codes_contiguous_and_fit_state_byte();
  test_catalog_released_slugs_append_only();
  test_catalog_enum_names_match_codes();
  test_catalog_slug_unknown_codes_null();
  return 0;
}
