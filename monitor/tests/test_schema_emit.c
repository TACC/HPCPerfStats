#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats.h"
#include "stats_text_format.h"

struct emit_capture {
  char buf[4096];
  size_t len;
};

static void capture_emit(void *opaque, const char *fmt, ...)
{
  va_list ap;
  struct emit_capture *cap = opaque;
  int n;

  va_start(ap, fmt);
  n = vsnprintf(cap->buf + cap->len, sizeof(cap->buf) - cap->len, fmt, ap);
  va_end(ap);
  if (n > 0)
    cap->len += (size_t) n;
}

static void test_emit_property_banner(void)
{
  struct emit_capture cap;

  memset(&cap, 0, sizeof(cap));
  stats_format_emit_property_banner(capture_emit, &cap, '$', "hpcperfstats", "1.2.3",
                                    "node1", "Linux", "aarch64", "6.1.0", "#1",
                                    12345ULL);
  assert(strstr(cap.buf, "$hpcperfstats 1.2.3\n") != NULL);
  assert(strstr(cap.buf, "$hostname node1\n") != NULL);
  assert(strstr(cap.buf, "$uname Linux aarch64 6.1.0 #1\n") != NULL);
  assert(strstr(cap.buf, "$uptime 12345\n") != NULL);

  cap.len = 0;
  cap.buf[0] = '\0';
  stats_format_emit_property_banner(NULL, &cap, '$', "x", "y", "z", "L", "m", "r",
                                    "v", 0ULL);
  assert(cap.len == 0);
}

static void test_emit_schema_line(void)
{
  struct stats_type type;
  struct emit_capture cap;

  memset(&type, 0, sizeof(type));
  snprintf(type.st_name, sizeof(type.st_name), "%s", "host_mem");
  assert(schema_init(&type.st_schema, "mem_total,E,U=kB mem_free,E") == 0);

  memset(&cap, 0, sizeof(cap));
  stats_format_emit_schema_line(capture_emit, &cap, '!', &type);
  assert(strstr(cap.buf, "!host_mem") != NULL);
  assert(strstr(cap.buf, " mem_total,E,U=kB") != NULL);
  assert(strstr(cap.buf, " mem_free,E\n") != NULL);

  cap.len = 0;
  cap.buf[0] = '\0';
  stats_format_emit_schema_line(NULL, &cap, '!', &type);
  assert(cap.len == 0);

  schema_destroy(&type.st_schema);
}

static void test_emit_mark_multiline(void)
{
  struct emit_capture cap;

  memset(&cap, 0, sizeof(cap));
  stats_format_emit_mark_multiline(capture_emit, &cap, '%', "one\ntwo");
  assert(strstr(cap.buf, "%one\n") != NULL);
  assert(strstr(cap.buf, "%two\n") != NULL);

  cap.len = 0;
  cap.buf[0] = '\0';
  stats_format_emit_mark_multiline(capture_emit, &cap, '%', "solo");
  assert(strstr(cap.buf, "%solo\n") != NULL);

  cap.len = 0;
  cap.buf[0] = '\0';
  stats_format_emit_mark_multiline(NULL, &cap, '%', "x");
  stats_format_emit_mark_multiline(capture_emit, &cap, '%', NULL);
  assert(cap.len == 0);
}

int main(void)
{
  test_emit_property_banner();
  test_emit_schema_line();
  test_emit_mark_multiline();
  printf("test_schema_emit passed\n");
  return 0;
}
