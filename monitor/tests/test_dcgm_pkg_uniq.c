#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_counter_metrics_dcgm_util.h"

static int cmp_int(const void *a, const void *b)
{
  int x = *(const int *)a;
  int y = *(const int *)b;

  if (x < y)
    return -1;
  if (x > y)
    return 1;
  return 0;
}

int main(void)
{
  int a[] = {0, 1, 0, 1};
  int b[] = {7};
  int c[] = {3, 3, 3};

  qsort(a, 4, sizeof(a[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(a, 4) == 2);
  qsort(b, 1, sizeof(b[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(b, 1) == 1);
  qsort(c, 3, sizeof(c[0]), cmp_int);
  assert(dcgm_count_unique_sorted_ints(c, 3) == 1);
  printf("test_dcgm_pkg_uniq passed\n");
  return 0;
}
