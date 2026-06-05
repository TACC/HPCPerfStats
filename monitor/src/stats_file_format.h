/*
 * On-disk stats archive header parsing and mark/schema suffix helpers.
 */
#ifndef STATS_FILE_FORMAT_H
#define STATS_FILE_FORMAT_H

#include <stdio.h>

struct schema_entry;

typedef enum {
  STATS_FILE_HDR_UNKNOWN = -1,
  STATS_FILE_HDR_SCHEMA = '!',   /* type schema line */
  STATS_FILE_HDR_DEVICES = '@',
  STATS_FILE_HDR_COMMENT = '#',
  STATS_FILE_HDR_PROPERTY = '$',
  STATS_FILE_HDR_MARK = '%'
} stats_file_header_directive_kind_t;

int stats_file_validate_program_header(const char *path, char *line_buf);
void stats_file_fprint_schema_entry_suffix(FILE *f, struct schema_entry *se);

stats_file_header_directive_kind_t stats_file_classify_header_directive(int lead_char);

void stats_file_fprint_mark_multiline(FILE *f, int mark_char, const char *payload);

#endif
