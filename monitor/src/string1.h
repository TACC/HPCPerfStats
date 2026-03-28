#ifndef _STRING1_H_
#define _STRING1_H_
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <ctype.h>

/* Leading/trailing isspace(3); safe for in-place trim of strdup'd config fields. */
static inline void str_trim_inplace(char *s)
{
  char *p;
  size_t len;

  if (s == NULL || *s == '\0')
    return;
  p = s;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  if (p != s)
    memmove(s, p, strlen(p) + 1);
  len = strlen(s);
  while (len > 0 && isspace((unsigned char)s[len - 1]))
    s[--len] = '\0';
}

static inline char *strsep_ne(char **ref, const char *delim)
{
  char *str;
  do
    str = strsep(ref, delim);
  while (str != NULL && *str == 0);
  return str;
}

static inline char *wsep(char **ref)
{
  return strsep_ne(ref, " \t\n\v\f\r");
}

static inline char *strf(const char *fmt, ...)
{
  char *str = NULL;
  va_list args;

  va_start(args, fmt);
  if (vasprintf(&str, fmt, args) < 0)
    str = NULL;
  va_end(args);
  return str;
}

#endif
