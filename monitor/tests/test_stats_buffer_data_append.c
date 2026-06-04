#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "stats_buffer_data_append.h"

int main(void)
{
  char *d = strdup("");
  assert(d != NULL);
  size_t len = 0;
  size_t cap = 1;

  assert(stats_buffer_data_append_fmt(&d, &len, &cap, "%s", "ab") == 0);
  assert(len == 2u && strcmp(d, "ab") == 0);

  assert(stats_buffer_data_append_fmt(&d, &len, &cap, "%s", "cd") == 0);
  assert(len == 4u && strcmp(d, "abcd") == 0);

  for (int i = 0; i < 100; i++)
    assert(stats_buffer_data_append_fmt(&d, &len, &cap, "x") == 0);
  assert(len == 4u + 100u);
  assert(d[len] == '\0');

  assert(stats_buffer_data_append_bytes(&d, &len, &cap, "z", 1) == 0);
  assert(len == 4u + 100u + 1u && d[len] == '\0' && d[len - 1] == 'z');

  free(d);
  printf("test_stats_buffer_data_append passed\n");
  return 0;
}
