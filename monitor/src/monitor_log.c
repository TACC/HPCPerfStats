#include "monitor_log.h"

#include <stdarg.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>

static FILE *g_monitor_stream;

FILE *monitor_log_get_stream(void)
{
  return g_monitor_stream != NULL ? g_monitor_stream : stderr;
}

void monitor_log_set_stream(FILE *stream)
{
  g_monitor_stream = stream;
}

static void monitor_log_va_diag(const char *fmt, va_list ap)
{
#ifdef DEBUG
# ifdef RABBITMQ
  vfprintf(stdout, fmt, ap);
# else
  vfprintf(stderr, fmt, ap);
# endif
#else
  fprintf(stderr, "%s: ", program_invocation_short_name);
  vfprintf(stderr, fmt, ap);
#endif
}

void monitor_log_info(const char *fmt, ...)
{
  va_list ap;
  FILE *out = monitor_log_get_stream();

  va_start(ap, fmt);
  vfprintf(out, fmt, ap);
  va_end(ap);
}

void monitor_log_warn(const char *fmt, ...)
{
  va_list ap;

  va_start(ap, fmt);
#ifdef DEBUG
# ifdef RABBITMQ
  vfprintf(stdout, fmt, ap);
# else
  vfprintf(stderr, fmt, ap);
# endif
#else
# ifdef RABBITMQ
  {
    char buf[2048];

    vsnprintf(buf, sizeof(buf), fmt, ap);
    syslog(LOG_WARNING, "%s: %s", program_invocation_short_name, buf);
  }
# else
  fprintf(stderr, "%s: ", program_invocation_short_name);
  vfprintf(stderr, fmt, ap);
# endif
#endif
  va_end(ap);
}

void monitor_log_error(const char *fmt, ...)
{
  va_list ap;

  va_start(ap, fmt);
#ifdef DEBUG
# ifdef RABBITMQ
  vfprintf(stdout, fmt, ap);
# else
  vfprintf(stderr, fmt, ap);
# endif
#else
# ifdef RABBITMQ
  {
    char buf[2048];

    vsnprintf(buf, sizeof(buf), fmt, ap);
    syslog(LOG_ERR, "%s: %s", program_invocation_short_name, buf);
  }
# else
  fprintf(stderr, "%s: ", program_invocation_short_name);
  vfprintf(stderr, fmt, ap);
# endif
#endif
  va_end(ap);
}

#ifdef DEBUG
void monitor_log_debug(const char *fmt, ...)
{
  va_list ap;

  va_start(ap, fmt);
  monitor_log_va_diag(fmt, ap);
  va_end(ap);
}
#endif
