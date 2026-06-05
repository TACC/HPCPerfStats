#ifndef TEST_DEBUG_SHM_EMIT_FIXTURE_H_
#define TEST_DEBUG_SHM_EMIT_FIXTURE_H_

struct stats_type;

int test_debug_shm_emit_fixture_init(void);
void test_debug_shm_emit_fixture_teardown(void);
const struct stats_type *test_debug_shm_emit_fixture_type_by_name(const char *name);

#endif
