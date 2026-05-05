#ifndef _MONITOR_LOG_H_
#define _MONITOR_LOG_H_

#include <stdio.h>

/*
 * Unified logging facade for daemon foreground operational messages vs
 * diagnostics vs syslog-backed errors when compiled as RabbitMQ release.
 *
 * Policy summary:
 *   monitor_log_info: vfprintf to monitor_log stream if set, else stderr.
 *   monitor_log_warn / monitor_log_error: match trace.h ERROR backend
 *     (DEBUG+RABBITMQ → stdout, else syslog(RMQ,!DEBUG) or stderr).
 *   monitor_log_debug: DEBUG builds only; same sink as TRACE-style chatter.
 */

void monitor_log_set_stream(FILE *stream);

FILE *monitor_log_get_stream(void);

void monitor_log_info(const char *fmt, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

void monitor_log_warn(const char *fmt, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

void monitor_log_error(const char *fmt, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

#ifdef DEBUG
void monitor_log_debug(const char *fmt, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;
#else
static inline void monitor_log_debug(const char *fmt, ...)
{
  (void)fmt;
}
#endif

#endif
