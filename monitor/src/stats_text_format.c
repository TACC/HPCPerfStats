#include "stats_text_format.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats.h"

size_t stats_format_schema_entry_suffix(char *buf, size_t cap,
					struct schema_entry *se)
{
	char tmp[96];
	char *p = tmp;
	char *end = tmp + sizeof(tmp);

	*p = '\0';

	if (se->se_type == SE_CONTROL) {
		if ((size_t)(end - p) >= 3) {
			memcpy(p, ",C", 3);
			p += 2;
		}
	} else if (se->se_type == SE_EVENT) {
		if ((size_t)(end - p) >= 3) {
			memcpy(p, ",E", 3);
			p += 2;
		}
	}
	if (se->se_unit != NULL) {
		int n = snprintf(p, (size_t)(end - p), ",U=%s", se->se_unit);
		if (n > 0 && (size_t)n < (size_t)(end - p))
			p += n;
		else
			p = end;
	}
	if (se->se_width != 0) {
		int n = snprintf(p, (size_t)(end - p), ",W=%u", se->se_width);
		if (n > 0 && (size_t)n < (size_t)(end - p))
			p += n;
		else
			p = end;
	}

	{
		size_t len = (size_t)(p - tmp);
		if (buf != NULL && cap > len)
			memcpy(buf, tmp, len + 1);
		return len;
	}
}

void stats_format_emit_property_banner(stats_format_emit_fn emit, void *opaque,
				       int prop_char, const char *prog,
				       const char *vers, const char *nodename,
				       const char *sysname, const char *machine,
				       const char *release, const char *version,
				       unsigned long long uptime)
{
	emit(opaque, "%c%s %s\n", prop_char, prog, vers);
	emit(opaque, "%chostname %s\n", prop_char, nodename);
	emit(opaque, "%cuname %s %s %s %s\n", prop_char, sysname, machine,
	     release, version);
	emit(opaque, "%cuptime %llu\n", prop_char,
	     (unsigned long long)uptime);
}

void stats_format_emit_schema_line(stats_format_emit_fn emit, void *opaque,
				   int schema_char, struct stats_type *type)
{
	size_t j;

	emit(opaque, "%c%s", schema_char, type->st_name);
	for (j = 0; j < type->st_schema.sc_len; j++) {
		struct schema_entry *se = type->st_schema.sc_ent[j];
		char suf[96];

		emit(opaque, " %s", se->se_key);
		stats_format_schema_entry_suffix(suf, sizeof(suf), se);
		emit(opaque, "%s", suf);
	}
	emit(opaque, "\n");
}

void stats_format_emit_mark_multiline(stats_format_emit_fn emit, void *opaque,
				      int mark_char, const char *payload)
{
	const char *str;

	if (payload == NULL)
		return;

	str = payload;
	while (*str != '\0') {
		const char *eol = strchr(str, '\n');

		if (eol == NULL)
			eol = str + strlen(str);

		emit(opaque, "%c%.*s\n", mark_char, (int)(eol - str), str);
		str = eol;
		if (*str == '\n')
			str++;
	}
}

int stats_format_append_mark_va(char **markp, const char *fmt, va_list ap)
{
	char *suffix = NULL;
	char *merged = NULL;
	int n;

	if (markp == NULL || fmt == NULL)
		return -1;

	n = vasprintf(&suffix, fmt, ap);
	if (n < 0)
		return -1;

	if (suffix == NULL || suffix[0] == '\0') {
		free(suffix);
		return 0;
	}

	if (*markp == NULL || (*markp)[0] == '\0') {
		free(*markp);
		*markp = suffix;
		return 0;
	}

	if (asprintf(&merged, "%s\n%s", *markp, suffix) < 0) {
		free(suffix);
		return -1;
	}
	free(*markp);
	free(suffix);
	*markp = merged;
	return 0;
}

int stats_format_snprintf_stats_row(char *buf, size_t cap,
				    struct stats_type *type,
				    struct stats *stats)
{
	size_t k;
	int n = snprintf(buf, cap, "%s %s", type->st_name, stats->s_dev);
	if (n < 0)
		return -1;

	{
		size_t used = (size_t)n;
		char *p;
		size_t rem;

		if (used >= cap)
			return (int)used + 64;

		p = buf + used;
		rem = cap - used;

		for (k = 0; k < type->st_schema.sc_len; k++) {
			n = snprintf(p, rem, " %llu",
				     (unsigned long long)stats->s_val[k]);
			if (n < 0)
				return -1;
			if ((size_t)n >= rem)
				return (int)(used + (size_t)n + 64);
			p += n;
			rem -= (size_t)n;
			used += (size_t)n;
		}
		return (int)used;
	}
}

void stats_format_fprint_stats_row(FILE *f, struct stats_type *type,
				   struct stats *stats)
{
	char stackbuf[4096];
	int n = stats_format_snprintf_stats_row(stackbuf, sizeof(stackbuf),
						  type, stats);

	if (n >= 0 && (size_t)n < sizeof(stackbuf)) {
		fprintf(f, "%s\n", stackbuf);
		return;
	}

	fprintf(f, "%s %s", type->st_name, stats->s_dev);
	for (size_t k = 0; k < type->st_schema.sc_len; k++)
		fprintf(f, " %llu",
			(unsigned long long)stats->s_val[k]);
	fprintf(f, "\n");
}
