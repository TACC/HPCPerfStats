#ifndef _TRACE_H_
#define _TRACE_H_
#include <stdio.h>
#include <errno.h>
#include <stdlib.h>
#include <syslog.h>

#ifdef DEBUG
#define TRACE ERROR
#else
static inline void TRACE(const char *fmt, ...) { }
#endif

#ifdef RABBITMQ
#define logger syslog
#define logtag LOG_ERR
#else
#define logger fprintf
#define logtag stderr
#endif

#ifdef DEBUG
#define ERROR(fmt, ...) \
  logger(logtag, "%s:%d: " fmt, __func__, __LINE__, ##__VA_ARGS__)
#else
#define ERROR(fmt, ...) \
  logger(logtag, "%s: " fmt, program_invocation_short_name, ##__VA_ARGS__)
#endif

#define FATAL(fmt, ...) do { \
    ERROR(fmt, ##__VA_ARGS__); \
    exit(1);                   \
  } while (0)

#endif
