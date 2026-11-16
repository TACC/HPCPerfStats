#ifndef _PATH_READ_H_
#define _PATH_READ_H_

#include <stddef.h>

/* Shared file-read primitives for collect.c and pscanf.c; the options select
 * error reporting and path_open_fail_once integration. */

struct path_read_opts {
  /* Honour path_open_fail_once skip list before open() and record failure
   * on open() error. Used by /proc and /sys collectors that want repeated
   * misses to fall off the hot path. */
  unsigned skip_known_bad : 1;

  /* Log open/read failures through the ERROR() macro (collect.c style). When
   * disabled, callers are expected to inspect errno (pscanf.c style). */
  unsigned report_errors : 1;

  /* If set, path_read_small returns 1 when the file contents exceed the
   * caller's stack buffer instead of silently truncating. The caller can
   * then fall back to path_read_alloc(). */
  unsigned detect_overflow : 1;
};

/* Cap for path_read_alloc allocations. */
#define PATH_READ_ALLOC_MAX (1u << 20)

/* Read up to bufsz - 1 bytes into buf and NUL-terminate.
 *
 * Returns:
 *   0 on success (*out_len is set to the number of bytes read);
 *   1 if opts->detect_overflow is set and the file is larger than bufsz - 1;
 *  -1 on open or read error (errno is preserved).
 */
int path_read_small(const char *path, char *buf, size_t bufsz, size_t *out_len,
                    const struct path_read_opts *opts);

/* Read the entire file into a freshly allocated, NUL-terminated buffer.
 * Caller frees *out_buf on success. Honours PATH_READ_ALLOC_MAX.
 *
 * Returns 0 on success or -1 on error (errno preserved). */
int path_read_alloc(const char *path, char **out_buf, size_t *out_len,
                    const struct path_read_opts *opts);

#endif
