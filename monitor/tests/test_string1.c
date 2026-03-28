#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "string1.h"

static void test_wsep_splits_on_space_and_tab(void)
{
  char buf[] = "a b\tc\n";
  char *p = buf;
  assert(strcmp(wsep(&p), "a") == 0);
  assert(strcmp(wsep(&p), "b") == 0);
  assert(strcmp(wsep(&p), "c") == 0);
  assert(wsep(&p) == NULL);
}

static void test_wsep_skips_consecutive_delims(void)
{
  char buf[] = "  x   y  ";
  char *p = buf;
  assert(strcmp(wsep(&p), "x") == 0);
  assert(strcmp(wsep(&p), "y") == 0);
  assert(wsep(&p) == NULL);
}

static void test_strsep_ne_skips_empty_tokens(void)
{
  char buf[] = "a,,b";
  char *p = buf;
  assert(strcmp(strsep_ne(&p, ","), "a") == 0);
  assert(strcmp(strsep_ne(&p, ","), "b") == 0);
  assert(strsep_ne(&p, ",") == NULL);
}

static void test_str_trim_inplace_strips_tabs_ends(void)
{
  char h[] = "\tstats.example\t\n";
  str_trim_inplace(h);
  assert(strcmp(h, "stats.example") == 0);
}

int main(void)
{
  test_wsep_splits_on_space_and_tab();
  test_wsep_skips_consecutive_delims();
  test_strsep_ne_skips_empty_tokens();
  test_str_trim_inplace_strips_tabs_ends();
  printf("test_string1 passed\n");
  return 0;
}
