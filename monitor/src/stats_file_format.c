#include <stdio.h>
#include <string.h>

#include "schema.h"
#include "stats_file_format.h"
#include "string1.h"
#include "trace.h"

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
  if (se->se_type == SE_CONTROL)
    fprintf(f, ",C");
  if (se->se_type == SE_EVENT)
    fprintf(f, ",E");
  if (se->se_unit != NULL)
    fprintf(f, ",U=%s", se->se_unit);
  if (se->se_width != 0)
    fprintf(f, ",W=%u", se->se_width);
}
