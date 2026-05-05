#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "schema.h"
#include "stats_file_format.h"
#include "stats_text_format.h"
#include "string1.h"
#include "trace.h"

stats_file_header_directive_kind_t stats_file_classify_header_directive(int lead_char)
{
	switch (lead_char) {
	case (int)STATS_FILE_HDR_SCHEMA:
		return STATS_FILE_HDR_SCHEMA;
	case (int)STATS_FILE_HDR_DEVICES:
		return STATS_FILE_HDR_DEVICES;
	case (int)STATS_FILE_HDR_COMMENT:
		return STATS_FILE_HDR_COMMENT;
	case (int)STATS_FILE_HDR_PROPERTY:
		return STATS_FILE_HDR_PROPERTY;
	case (int)STATS_FILE_HDR_MARK:
		return STATS_FILE_HDR_MARK;
	default:
		return STATS_FILE_HDR_UNKNOWN;
	}
}

static void stats_file_fmt_emit(void *opaque, const char *fmt, ...)
{
	FILE *f = opaque;
	va_list ap;

	va_start(ap, fmt);
	vfprintf(f, fmt, ap);
	va_end(ap);
}

void stats_file_fprint_mark_multiline(FILE *f, int mark_char, const char *payload)
{
	if (f == NULL)
		return;
	stats_format_emit_mark_multiline(stats_file_fmt_emit, f, mark_char,
				       payload);
}

int stats_file_validate_program_header(const char *path, char *line_buf)
{
#define SF_PROPERTY_CHAR '$'
	char *line = line_buf;

	if (*(line++) != SF_PROPERTY_CHAR) {
		ERROR("file `%s' is not in %s format\n", path, STATS_PROGRAM);
		return -1;
	}

	char *prog = wsep(&line);

	if (prog == NULL || strcmp(prog, STATS_PROGRAM) != 0) {
		ERROR("file `%s' is not in %s format\n", path, STATS_PROGRAM);
		return -1;
	}

	char *vers = wsep(&line);

	if (vers == NULL || strverscmp(vers, STATS_VERSION) > 0) {
		ERROR("file `%s' is has unsupported version `%s'\n", path,
		      vers != NULL ? vers : "NULL");
		return -1;
	}

	TRACE("prog %s, vers %s\n", prog, vers);
	return 0;
#undef SF_PROPERTY_CHAR
}

void stats_file_fprint_schema_entry_suffix(FILE *f, struct schema_entry *se)
{
	char buf[96];
	size_t n = stats_format_schema_entry_suffix(buf, sizeof(buf), se);

	if (n >= sizeof(buf))
		return;
	fputs(buf, f);
}
