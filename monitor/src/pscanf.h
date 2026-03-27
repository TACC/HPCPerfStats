#ifndef _PSCANF_H_
#define _PSCANF_H_

int pscanf(const char *path, const char *fmt, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(scanf, 2, 3)))
#endif
    ;

#endif
