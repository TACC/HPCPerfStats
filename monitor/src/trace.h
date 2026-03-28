#ifndef _TRACE_H_
#define _TRACE_H_
#include <stdio.h>
#include <errno.h>
#include <stdlib.h>
#include <syslog.h>

#ifdef DEBUG
# ifdef RABBITMQ
/* Daemon: release uses syslog; DEBUG mirrors diagnostics to stdout for foreground runs. */
#  define ERROR(fmt, ...) \
    fprintf(stdout, "%s:%d: " fmt, __func__, __LINE__, ##__VA_ARGS__)
# else
#  define ERROR(fmt, ...) \
    fprintf(stderr, "%s:%d: " fmt, __func__, __LINE__, ##__VA_ARGS__)
# endif
# define TRACE ERROR
#else
static inline void TRACE(const char *fmt, ...) { (void)fmt; }
# ifdef RABBITMQ
#  define ERROR(fmt, ...) \
    syslog(LOG_ERR, "%s: " fmt, program_invocation_short_name, ##__VA_ARGS__)
# else
#  define ERROR(fmt, ...) \
    fprintf(stderr, "%s: " fmt, program_invocation_short_name, ##__VA_ARGS__)
# endif
#endif

#define FATAL(fmt, ...) do { \
    ERROR(fmt, ##__VA_ARGS__); \
    exit(1);                   \
  } while (0)

#endif
