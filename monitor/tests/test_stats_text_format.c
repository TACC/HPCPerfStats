#include <assert.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats.h"
#include "stats_text_format.h"

static void append_mark_helper(char **m, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	assert(stats_format_append_mark_va(m, fmt, ap) == 0);
	va_end(ap);
}

/* Build a struct stats for `type` with the given values; mark all present. */
static struct stats *make_test_stats(struct stats_type *type, const char *dev,
				     const unsigned long long *vals)
{
	size_t n = type->st_schema.sc_len;
	struct stats *s = malloc(sizeof(*s) + strlen(dev) + 1);
	size_t k;

	assert(s != NULL);
	s->s_type = type;
	s->s_val = malloc(n * sizeof(*s->s_val));
	s->s_val_present = malloc(n);
	assert(s->s_val != NULL && s->s_val_present != NULL);
	for (k = 0; k < n; k++) {
		s->s_val[k] = vals[k];
		s->s_val_present[k] = 1;
	}
	strcpy(s->s_dev, dev);
	return s;
}

static void free_test_stats(struct stats *s)
{
	free(s->s_val);
	free(s->s_val_present);
	free(s);
}

static void test_snprintf_stats_row_sparse_tiers(void)
{
  struct stats_type type;
  const unsigned long long vals[4] = { 100, 200, 300, 400 };
  struct stats *s;
  char buf[256];

  memset(&type, 0, sizeof(type));
  snprintf(type.st_name, sizeof(type.st_name), "%s", "host_tt");
  assert(schema_init(&type.st_schema, "a,E b,E,R=S c,E d,E,R=S") == 0);
  assert(type.st_schema.sc_len == 4);

  s = make_test_stats(&type, "dev0", vals);

  assert(stats_format_snprintf_stats_row_tier(buf, sizeof(buf), &type, s,
                                              STATS_ROW_FAST) > 0);
  assert(strcmp(buf, "host_tt dev0 @fast 100 300") == 0);

  assert(stats_format_snprintf_stats_row_tier(buf, sizeof(buf), &type, s,
                                              STATS_ROW_FULL) > 0);
  assert(strcmp(buf, "host_tt dev0 @full 100 200 300 400") == 0);

  assert(stats_format_snprintf_stats_row(buf, sizeof(buf), &type, s) > 0);
  assert(strstr(buf, "@fast") == NULL);
  assert(strstr(buf, "@full") == NULL);
  assert(strcmp(buf, "host_tt dev0 100 200 300 400") == 0);

  assert(stats_format_snprintf_stats_row(NULL, sizeof(buf), &type, s) == -1);
  assert(stats_format_snprintf_stats_row(buf, sizeof(buf), NULL, s) == -1);
  assert(stats_format_snprintf_stats_row(buf, sizeof(buf), &type, NULL) == -1);

  free_test_stats(s);
  schema_destroy(&type.st_schema);
}

/* Two fast keys (a, c) and two slow keys (b, d). */
static void test_sparse_rows(void)
{
	struct stats_type type;
	const unsigned long long vals[4] = { 100, 200, 300, 400 };
	struct stats *s;
	char buf[256];

	memset(&type, 0, sizeof(type));
	snprintf(type.st_name, sizeof(type.st_name), "%s", "host_tt");
	assert(schema_init(&type.st_schema, "a,E b,E,R=S c,E d,E,R=S") == 0);
	assert(type.st_schema.sc_len == 4);

	s = make_test_stats(&type, "dev0", vals);

	assert(stats_format_snprintf_stats_row_tier(buf, sizeof(buf), &type, s,
						    STATS_ROW_LEGACY) > 0);
	assert(strcmp(buf, "host_tt dev0 100 200 300 400") == 0);

	assert(stats_format_snprintf_stats_row_tier(buf, sizeof(buf), &type, s,
						    STATS_ROW_FULL) > 0);
	assert(strcmp(buf, "host_tt dev0 @full 100 200 300 400") == 0);

	assert(stats_format_snprintf_stats_row_tier(buf, sizeof(buf), &type, s,
						    STATS_ROW_FAST) > 0);
	assert(strcmp(buf, "host_tt dev0 @fast 100 300") == 0);

	/* The legacy wrapper must stay byte-identical to the old format. */
	assert(stats_format_snprintf_stats_row(buf, sizeof(buf), &type, s) > 0);
	assert(strcmp(buf, "host_tt dev0 100 200 300 400") == 0);

	free_test_stats(s);
	schema_destroy(&type.st_schema);
}

static void test_schema_line_with_slow_suffix(void)
{
	char *line = strdup("rx_bytes,E,U=B,R=S");
	struct schema_entry *se = parse_schema_entry(line);
	char suf[64];

	free(line);
	assert(se != NULL);
	assert(stats_format_schema_entry_suffix(suf, sizeof(suf), se) > 0);
	assert(strcmp(suf, ",E,U=B,R=S") == 0);
	free(se->se_unit);
	free(se->se_desc);
	free(se);
}

int main(void)
{
	char blob_control[128];
	struct schema_entry *se_control =
	    (struct schema_entry *)(void *)blob_control;

	memset(blob_control, 0, sizeof(blob_control));
	se_control->se_type = SE_CONTROL;
	se_control->se_unit = NULL;
	se_control->se_width = 0;
	strcpy(se_control->se_key, "freq_max_temp_cycles");

	{
		char suf[32];
		size_t n = stats_format_schema_entry_suffix(suf, sizeof(suf),
							    se_control);

		assert(n == 2u);
		assert(strcmp(suf, ",C") == 0);
	}

	{
		unsigned char blob_ev[160];
		struct schema_entry *se_ev =
		    (struct schema_entry *)(void *)blob_ev;

		memset(blob_ev, 0, sizeof(blob_ev));
		se_ev->se_type = SE_EVENT;
		se_ev->se_unit = (char *)"mJ";
		se_ev->se_width = 32;
		strcpy(se_ev->se_key, "pkg_energy");

		char suf[64];
		size_t n = stats_format_schema_entry_suffix(suf, sizeof(suf),
							    se_ev);

		assert(strcmp(suf, ",E,U=mJ,W=32") == 0);
		assert(n == strlen(suf));
	}

	{
		char *m = NULL;

		append_mark_helper(&m, "%s", "first");
		assert(m != NULL && strcmp(m, "first") == 0);

		append_mark_helper(&m, "%s", "second");
		assert(strcmp(m, "first\nsecond") == 0);

		free(m);
		m = strdup("");
		assert(m != NULL);
		append_mark_helper(&m, "%s", "after_empty");
		assert(strcmp(m, "after_empty") == 0);
		free(m);
	}

	test_sparse_rows();
	test_snprintf_stats_row_sparse_tiers();
	test_schema_line_with_slow_suffix();

	printf("test_stats_text_format passed\n");
	return 0;
}
