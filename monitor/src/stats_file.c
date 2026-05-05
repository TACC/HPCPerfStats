#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <sys/utsname.h>
#include "stats.h"
#include "stats_file.h"
#include "stats_file_format.h"
#include "stats_text_format.h"
#include "schema.h"
#include "trace.h"
#include "pscanf.h"
#include "fileio.h"
#include "path_open_fail_once.h"
#include "string1.h"

#define SF_SCHEMA_CHAR '!'
#define SF_DEVICES_CHAR '@'
#define SF_COMMENT_CHAR '#'
#define SF_PROPERTY_CHAR '$'
#define SF_MARK_CHAR '%'

#define sf_printf(sf, fmt, args...) fprintf(sf->sf_file, fmt, ##args)

static void stats_file_emit(void *opaque, const char *fmt, ...)
{
  struct stats_file *sf = opaque;
  va_list ap;

  va_start(ap, fmt);
  vfprintf(sf->sf_file, fmt, ap);
  va_end(ap);
}

static void stats_file_write_property_banner(struct stats_file *sf)
{
  struct utsname uts_buf;
  unsigned long long uptime = 0;

  uname(&uts_buf);
  pscanf("/proc/uptime", "%llu", &uptime);

  stats_format_emit_property_banner(stats_file_emit, sf, SF_PROPERTY_CHAR,
				    STATS_PROGRAM, STATS_VERSION,
				    uts_buf.nodename, uts_buf.sysname,
				    uts_buf.machine, uts_buf.release,
				    uts_buf.version, uptime);
}

static int sf_rd_dispatch_header_line(struct stats_file *sf, char *first, char *line, int line_nr)
{
  struct stats_type *type;
  stats_file_header_directive_kind_t kind =
      stats_file_classify_header_directive((unsigned char)*first);

  TRACE("%s:%d: first `%s', rest `%s'\n", sf->sf_path, line_nr, first, line);
  switch (kind) {
  case STATS_FILE_HDR_SCHEMA:
    type = stats_type_get(first + 1);
    if (type == NULL) {
      ERROR("%s:%d: unknown type `%s'\n", sf->sf_path, line_nr, first + 1);
      return -1;
    }
    type->st_schema_def = strdup(line);
    type->st_enabled = 1;
    break;
  case STATS_FILE_HDR_DEVICES:
  case STATS_FILE_HDR_COMMENT:
  case STATS_FILE_HDR_PROPERTY:
  case STATS_FILE_HDR_MARK:
    break;
  default:
    ERROR("%s:%d: bad directive `%s %s'\n", sf->sf_path, line_nr, first, line);
    return -1;
  }
  return 0;
}

static int sf_rd_hdr(struct stats_file *sf)
{
  int rc = 0;
  char *line_buf = NULL, *line;
  size_t line_buf_size = 0;

  if (getline(&line_buf, &line_buf_size, sf->sf_file) <= 0) {
    if (feof(sf->sf_file)) {
      sf->sf_empty = 1;
      goto out;
    }
    goto err;
  }

  if (stats_file_validate_program_header(sf->sf_path, line_buf) < 0)
    goto err;

  int nr = 1;
  while (getline(&line_buf, &line_buf_size, sf->sf_file) > 0) {
    nr++;
    line = line_buf;

    char *first = wsep(&line);
    if (first == NULL)
      break;

    if (sf_rd_dispatch_header_line(sf, first, line, nr) < 0)
      goto err;
  }

 out:
  if (ferror(sf->sf_file)) {
  err:
    rc = -1;
    if (errno == 0)
      errno = EINVAL;
  }

  if (ferror(sf->sf_file))
    ERROR("error reading from `%s': %m\n", sf->sf_path);

  free(line_buf);
  return rc;
}

static int sf_wr_hdr(struct stats_file *sf)
{
  stats_file_write_property_banner(sf);

  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;

    TRACE("type %s, schema_len %zu\n", type->st_name, type->st_schema.sc_len);
    stats_format_emit_schema_line(stats_file_emit, sf, SF_SCHEMA_CHAR, type);
  }

  fflush(sf->sf_file);

  return 0;
}

int stats_file_open(struct stats_file *sf, const char *path)
{
  memset(sf, 0, sizeof(*sf));

  sf->sf_path = strdup(path);
  if (sf->sf_path == NULL) {
    ERROR("cannot create path: %m\n");
    return -1;
  }

  sf->sf_file = path_file_fopen_append(sf->sf_path);
  if (sf->sf_file == NULL) {
    free(sf->sf_path);
    sf->sf_path = NULL;
    return -1;
  }

  if (sf_rd_hdr(sf) < 0) {
    fclose(sf->sf_file);
    sf->sf_file = NULL;
    free(sf->sf_path);
    sf->sf_path = NULL;
    return -1;
  }

  return 0;
}

int stats_file_mark(struct stats_file *sf, const char *fmt, ...)
{
  /* TODO Concatenate new mark with old. */
  va_list args;
  va_start(args, fmt);

  if (vasprintf(&sf->sf_mark, fmt, args) < 0)
    sf->sf_mark = NULL;

  va_end(args);

  return 0;
}

int stats_file_close(struct stats_file *sf)
{

  int rc = 0;
  if (sf->sf_empty)
    sf_wr_hdr(sf);

  fseek(sf->sf_file, 0, SEEK_END);

  struct utsname uts_buf;
  uname(&uts_buf);

  sf_printf(sf, "\n%f %s %s\n", current_time, jobid, uts_buf.nodename);

  if (sf->sf_mark != NULL)
    stats_file_fprint_mark_multiline(sf->sf_file, SF_MARK_CHAR, sf->sf_mark);

  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!(type->st_enabled && type->st_selected))
      continue;

    size_t j = 0;
    char *dev;
    while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL) {
      struct stats *stats = key_to_stats(dev);

      stats_format_fprint_stats_row(sf->sf_file, type, stats);
    }
  }

  if (ferror(sf->sf_file)) {
    ERROR("error writing to `%s': %m\n", sf->sf_path);
    rc = -1;
  }

  if (fclose(sf->sf_file) < 0) {
    ERROR("error closing `%s': %m\n", sf->sf_path);
    rc = -1;
  }

  free(sf->sf_path);
  free(sf->sf_mark);
  memset(sf, 0, sizeof(struct stats_file));

  return rc;
}
