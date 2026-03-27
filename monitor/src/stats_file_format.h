#ifndef STATS_FILE_FORMAT_H
#define STATS_FILE_FORMAT_H

#include <stdio.h>

struct schema_entry;

int stats_file_validate_program_header(const char *path, char *line_buf);
void stats_file_fprint_schema_entry_suffix(FILE *f, struct schema_entry *se);

#endif
