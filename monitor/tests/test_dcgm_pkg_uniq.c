/* Mirrors cpu_counter_metrics.c package-id dedupe used for DCGM_FE_CPU mapping. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int cmp_int(const void *a, const void *b)
{
  int x = *(const int *) a;
  int y = *(const int *) b;

  if (x < y)
    return -1;
  if (x > y)
    return 1;
  return 0;
}

static int count_unique_sorted(const int *sorted, int n)
{
  int i, nu;

  if (n <= 0)
    return 0;
  nu = 1;
  for (i = 1; i < n; i++) {
    if (sorted[i] != sorted[i - 1])
      nu++;
  }
  return nu;
}

int main(void)
{
  int a[] = { 0, 1, 0, 1 };
  int b[] = { 7 };
  int c[] = { 3, 3, 3 };

  qsort(a, 4, sizeof(a[0]), cmp_int);
  assert(count_unique_sorted(a, 4) == 2);
  qsort(b, 1, sizeof(b[0]), cmp_int);
  assert(count_unique_sorted(b, 1) == 1);
  qsort(c, 3, sizeof(c[0]), cmp_int);
  assert(count_unique_sorted(c, 3) == 1);
  printf("test_dcgm_pkg_uniq passed\n");
  return 0;
}
