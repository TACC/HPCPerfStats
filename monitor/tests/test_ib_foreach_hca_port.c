/* ib_foreach_hca_port walks /sys/class/infiniband (hardcoded): a NULL callback is EINVAL, and the
 * walk's return reports whether that directory opened, so host_ib can mark its rows when it did
 * not. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <unistd.h>

#include "ib_common.h"

static void count_port(const char *hca, int port, void *ctx)
{
  (void)hca;
  (void)port;
  (*(int *)ctx)++;
}

static void test_null_fn_is_einval(void)
{
  errno = 0;
  assert(ib_foreach_hca_port(NULL, NULL) == -1);
  assert(errno == EINVAL);
}

/* Regression: the walk returned void, so host_ib never saw an opendir failure. */
static void test_walk_reports_open_failure(void)
{
  int ports = 0;
  int opened = access("/sys/class/infiniband", R_OK | X_OK) == 0;

  errno = 0;
  assert(ib_foreach_hca_port(count_port, &ports) == (opened ? 0 : -1));
  assert(opened || (errno != 0 && ports == 0));
}

int main(void)
{
  test_null_fn_is_einval();
  test_walk_reports_open_failure();
  printf("test_ib_foreach_hca_port passed\n");
  return 0;
}
