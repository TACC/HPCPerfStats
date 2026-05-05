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

static void test_parse_U_without_equals_skips(void)
{
  char *line = strdup("k,U,E");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(strcmp(se->se_key, "k") == 0);
  assert(se->se_unit == NULL);
  assert(se->se_type == SE_EVENT);
  free_entry(se);
}

static void test_parse_W_hex(void)
{
  char *line = strdup("k,W=0x10");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(se->se_width == 16u);
  free_entry(se);
}

static void test_parse_U_empty_value(void)
{
  char *line = strdup("k,U=");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(se->se_unit != NULL);
  assert(strcmp(se->se_unit, "") == 0);
  free_entry(se);
}

static void test_schema_flag_csv_strict(void)
{
  char *line = strdup("k,,E");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);
  assert(se == NULL);

  line = strdup("port_xmit_wait,E,,W=32,U=ms");
  se = parse_schema_entry(line);
  free(line);
  assert(se == NULL);

  line = strdup("port_xmit_wait,E,W=32,U=ms");
  se = parse_schema_entry(line);
  free(line);
  assert(se != NULL);
  assert(strcmp(se->se_key, "port_xmit_wait") == 0);
  assert(se->se_type == SE_EVENT);
  assert(se->se_width == 32u);
  assert(se->se_unit != NULL && strcmp(se->se_unit, "ms") == 0);
  free_entry(se);
}

static void test_parse_C_then_E_last_wins(void)
{
  char *line = strdup("k,C,E");
  struct schema_entry *se = parse_schema_entry(line);
  free(line);

  assert(se != NULL);
  assert(se->se_type == SE_EVENT);
  free_entry(se);
}

static void test_schema_init_aborts_cleanly_on_second_invalid_token(void)
{
  struct schema sc;

  memset(&sc, 0, sizeof(sc));
  /* First token parses; second token ",E" has empty key -> parse_schema_entry NULL. */
  assert(schema_init(&sc, "cpu,E ,E") < 0);
  schema_destroy(&sc);
}

int main(void)
{
  test_schema_init_aborts_cleanly_on_second_invalid_token();
  test_parse_event_unit_width();
  test_parse_control();
  test_parse_empty_key_returns_null();
  test_parse_unknown_option_ignored();
  test_parse_U_without_equals_skips();
  test_parse_W_hex();
  test_parse_U_empty_value();
  test_schema_flag_csv_strict();
  test_parse_C_then_E_last_wins();
  printf("test_schema_parse passed\n");
  return 0;
}
