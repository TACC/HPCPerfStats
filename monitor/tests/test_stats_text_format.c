#include <assert.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats_text_format.h"

static void append_mark_helper(char **m, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	assert(stats_format_append_mark_va(m, fmt, ap) == 0);
	va_end(ap);
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
	strcpy(se_control->se_key, "FREQ_MAX_TEMP_CYCLES");

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
		strcpy(se_ev->se_key, "E");

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

	printf("test_stats_text_format passed\n");
	return 0;
}
