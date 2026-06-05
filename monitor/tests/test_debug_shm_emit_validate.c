/*
 * Structural checks for debug-shm sample payloads (driver row shape).
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_text_format.h"
#include "test_debug_shm_emit_fixture.h"
#include "test_debug_shm_emit_validate.h"

static int split_fields(char *line, char **fields, size_t max_fields, size_t *nfields)
{
  char *p = line;
  size_t n = 0;

  while (n < max_fields) {
    while (*p == ' ' || *p == '\t')
      p++;
    if (*p == '\0')
      break;
    fields[n++] = p;
    while (*p != '\0' && *p != ' ' && *p != '\t')
      p++;
    if (*p == '\0')
      break;
    *p++ = '\0';
  }
  *nfields = n;
  return 0;
}

static int token_is_uint(const char *tok)
{
  size_t i;

  if (tok == NULL || tok[0] == '\0')
    return 0;
  for (i = 0; tok[i] != '\0'; i++) {
    if (!isdigit((unsigned char) tok[i]))
      return 0;
  }
  return 1;
}

static int st_name_looks_like_driver(const char *name)
{
  size_t i;

  if (name == NULL || name[0] == '\0')
    return 0;
  if (!islower((unsigned char) name[0]))
    return 0;
  for (i = 0; name[i] != '\0'; i++) {
    unsigned char c = (unsigned char) name[i];

    if (!(islower(c) || isdigit(c) || c == '_'))
      return 0;
  }
  return 1;
}

static int dev_looks_plausible(const char *dev)
{
  size_t i;

  if (dev == NULL || dev[0] == '\0')
    return 0;
  for (i = 0; dev[i] != '\0'; i++) {
    if (isspace((unsigned char) dev[i]))
      return 0;
  }
  return 1;
}

static size_t schema_value_count_for_tier(const struct stats_type *type,
					enum stats_row_tier tier)
{
  size_t k;
  size_t n = 0;

  if (type == NULL)
    return 0;
  for (k = 0; k < type->st_schema.sc_len; k++) {
    if (tier == STATS_ROW_FAST
	&& type->st_schema.sc_ent[k]->se_collect_tier == COLLECT_TIER_SLOW)
      continue;
    n++;
  }
  return n;
}

static int validate_sample_header(const char *line)
{
  char buf[256];
  char *fields[8];
  size_t nfields = 0;
  char *end;

  if (line == NULL)
    return -1;
  snprintf(buf, sizeof(buf), "%s", line);
  split_fields(buf, fields, 8, &nfields);
  if (nfields < 3) {
    fprintf(stderr, "emit_validate: sample header needs timestamp jobid host\n");
    return -1;
  }
  strtod(fields[0], &end);
  if (end == fields[0] || *end != '\0') {
    fprintf(stderr, "emit_validate: header timestamp not numeric: %s\n", fields[0]);
    return -1;
  }
  if (fields[1][0] == '\0' || fields[2][0] == '\0') {
    fprintf(stderr, "emit_validate: header jobid/host empty\n");
    return -1;
  }
  return 0;
}

static int validate_driver_row(const char *line, enum stats_row_tier payload_tier)
{
  char buf[512];
  char *fields[64];
  size_t nfields = 0;
  size_t value_start;
  size_t value_count;
  size_t expect;
  const struct stats_type *type;
  enum stats_row_tier row_tier;
  size_t i;

  snprintf(buf, sizeof(buf), "%s", line);
  split_fields(buf, fields, 64, &nfields);
  if (nfields < 3) {
    fprintf(stderr, "emit_validate: row too short: %s\n", line);
    return -1;
  }
  if (!st_name_looks_like_driver(fields[0])) {
    fprintf(stderr, "emit_validate: type name not driver-shaped: %s\n", fields[0]);
    return -1;
  }
  if (!dev_looks_plausible(fields[1])) {
    fprintf(stderr, "emit_validate: device not plausible for %s\n", fields[0]);
    return -1;
  }

  type = test_debug_shm_emit_fixture_type_by_name(fields[0]);
  if (type == NULL) {
    fprintf(stderr, "emit_validate: unknown driver type in row: %s\n", fields[0]);
    return -1;
  }

  if (collect_tier_enabled()) {
    if (strcmp(fields[2], "@fast") == 0) {
      row_tier = STATS_ROW_FAST;
      value_start = 3;
    } else if (strcmp(fields[2], "@full") == 0) {
      row_tier = STATS_ROW_FULL;
      value_start = 3;
    } else {
      fprintf(stderr, "emit_validate: %s missing @fast/@full token: %s\n",
	      fields[0], line);
      return -1;
    }
    if (row_tier != payload_tier) {
      fprintf(stderr, "emit_validate: %s tier %s does not match payload tier\n",
	      fields[0], fields[2]);
      return -1;
    }
  } else {
    row_tier = STATS_ROW_LEGACY;
    value_start = 2;
  }

  if (value_start >= nfields) {
    fprintf(stderr, "emit_validate: %s row has no values: %s\n", fields[0], line);
    return -1;
  }
  value_count = nfields - value_start;
  expect = schema_value_count_for_tier(type, row_tier);
  if (value_count != expect) {
    fprintf(stderr,
	    "emit_validate: %s dev=%s expected %zu values for %s row, got %zu: %s\n",
	    fields[0], fields[1], expect,
	    row_tier == STATS_ROW_FAST ? "@fast" : "@full",
	    value_count, line);
    return -1;
  }
  for (i = value_start; i < nfields; i++) {
    if (!token_is_uint(fields[i])) {
      fprintf(stderr, "emit_validate: %s non-numeric value %s in %s\n",
	      fields[0], fields[i], line);
      return -1;
    }
  }
  return 0;
}

int test_debug_shm_emit_validate_payload(const char *payload, size_t len,
					 enum stats_row_tier tier)
{
  char *copy;
  char *line;
  char *save = NULL;
  int saw_header = 0;
  int saw_row = 0;
  int rc = 0;

  if (payload == NULL || len == 0) {
    fprintf(stderr, "emit_validate: empty payload\n");
    return -1;
  }
  copy = malloc(len + 1);
  if (copy == NULL)
    return -1;
  memcpy(copy, payload, len);
  copy[len] = '\0';

  for (line = copy; (line = strtok_r(line, "\n", &save)) != NULL; line = NULL) {
    if (line[0] == '\0')
      continue;
    if (!saw_header) {
      if (validate_sample_header(line) != 0) {
	rc = -1;
	break;
      }
      saw_header = 1;
      continue;
    }
    if (validate_driver_row(line, tier) != 0) {
      rc = -1;
      break;
    }
    saw_row = 1;
  }

  if (rc == 0 && !saw_header) {
    fprintf(stderr, "emit_validate: payload missing sample header\n");
    rc = -1;
  }
  if (rc == 0 && !saw_row) {
    fprintf(stderr, "emit_validate: payload has no driver sample rows\n");
    rc = -1;
  }

  free(copy);
  return rc;
}
