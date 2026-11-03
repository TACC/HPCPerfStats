/* Regression: test assertions must stay live in release builds (-DNDEBUG).
 * Built with -DNDEBUG on purpose; test_assert_live.h (force-included) undoes it. */
#include <assert.h>
#include <stdio.h>

int main(void)
{
  int ran = 0;

  assert(++ran == 1);
  if (ran != 1) {
    fprintf(stderr, "assert() was compiled out under -DNDEBUG\n");
    return 1;
  }
  printf("test_assert_live passed\n");
  return 0;
}
