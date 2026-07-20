/* Growable UTF-8 buffer helpers for RabbitMQ payload assembly. */
#ifndef STATS_BUFFER_DATA_APPEND_H
#define STATS_BUFFER_DATA_APPEND_H

#include <stdarg.h>
#include <stddef.h>

int stats_buffer_data_append_vfmt(char **data, size_t *len, size_t *cap, const char *fmt,
                                  va_list ap);
int stats_buffer_data_append_fmt(char **data, size_t *len, size_t *cap, const char *fmt, ...)
    __attribute__((format(printf, 4, 5)));
int stats_buffer_data_append_bytes(char **data, size_t *len, size_t *cap, const void *p, size_t n);

#endif
