#ifndef _PROCFILE_PARSE_H_
#define _PROCFILE_PARSE_H_

#include <stddef.h>

/* Shared helpers for /proc and /sys text files.
 *
 * Many TYPE drivers (mem, ps, sysv_shm, llite, mdc, lnet, etc.) repeated the
 * same open / setvbuf / getline / fclose dance. procfile_for_each_line
 * centralises that pattern; the caller-supplied callback receives one line
 * with the trailing newline stripped and is responsible for parsing it.
 *
 * proc_kv_into_stats is provided in <procfile_kv.h> so this header can be
 * used by callers (and tests) that do not want to link against stats.c.
 */

/* Callback: returns 0 to continue, non-zero to stop iteration. */
typedef int (*procfile_line_fn)(char *line, void *ctx);

/* Open path (honouring path_open_fail_once), iterate every line, then close
 * the file. Lines are NUL-terminated; trailing '\n' is stripped before cb
 * runs. cb may modify the buffer in place.
 *
 * Returns 0 on success (callback may have stopped early), -1 if the file
 * could not be opened. */
int procfile_for_each_line(const char *path, procfile_line_fn cb, void *ctx);

/* Like procfile_for_each_line but skips the first `skip` lines (e.g. the
 * header in /proc/sysvipc/shm). */
int procfile_for_each_line_skip(const char *path, size_t skip, procfile_line_fn cb, void *ctx);

#endif
