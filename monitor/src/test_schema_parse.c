#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"

static void free_entry(struct schema_entry *se)
{
  if (se == NULL)
    return;
  free(se->se_unit);
  free(se->se_desc);
  free(se);
}

static void test_parse_event_unit_width(void)
{
  char *line = strdup("  cpu,E,U=sec,W=8");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(strcmp(se->se_key, "cpu") == 0);
  assert(se->se_type == SE_EVENT);
  assert(se->se_unit != NULL && strcmp(se->se_unit, "sec") == 0);
  assert(se->se_width == 8u);
  free_entry(se);
}

static void test_parse_control(void)
{
  char *line = strdup("flag,C");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(strcmp(se->se_key, "flag") == 0);
  assert(se->se_type == SE_CONTROL);
  free_entry(se);
}

static void test_parse_empty_key_returns_null(void)
{
  char *line = strdup(",E");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);
  assert(se == NULL);
}

static void test_parse_unknown_option_ignored(void)
{
  char *line = strdup("x,Z=1,E");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(strcmp(se->se_key, "x") == 0);
  assert(se->se_type == SE_EVENT);
  free_entry(se);
}

int main(void)
{
  test_parse_event_unit_width();
  test_parse_control();
  test_parse_empty_key_returns_null();
  test_parse_unknown_option_ignored();
  printf("test_schema_parse passed\n");
  return 0;
}
