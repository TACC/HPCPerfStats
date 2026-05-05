#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "schema.h"
#include "stats_text_format.h"

int main(void)
{
	char blob_control[128];
	struct schema_entry *se_control =
	    (struct schema_entry *)(void *)blob_control;

	memset(blob_control, 0, sizeof(blob_control));
	se_control->se_type = SE_CONTROL;
	se_control->se_unit = NULL;
	se_control->se_width = 0;
	strcpy(se_control->se_key, "CTL0");

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

	printf("test_stats_text_format passed\n");
	return 0;
}
