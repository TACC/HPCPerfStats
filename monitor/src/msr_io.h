#ifndef _MSR_IO_H_
#define _MSR_IO_H_

#include <stdint.h>

/* Shared helpers for /dev/cpu/<n>/msr access.
 *
 * Many Intel/AMD PMC and uncore drivers repeated the same scaffolding:
 *
 *   snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
 *   if (path_open_is_skipped(msr_path)) goto out;
 *   fd = open(msr_path, flags);
 *   if (fd < 0) { path_open_record_failure_once(msr_path); goto out; }
 *
 * msr_open_cpu wraps the entire thing. msr_read_u64 / msr_write_u64 are thin
 * pread/pwrite wrappers that return -1 on failure with errno preserved.
 */

/* Open /dev/cpu/<cpu>/msr with the given open(2) flags.
 *
 * cpu may be a numeric string (the historical convention used by intel_pmc3
 * and amd64 drivers); any character set is accepted as long as the resulting
 * path resolves under /dev/cpu/.
 *
 * Honours path_open_fail_once: returns -1 immediately if the path was
 * recently recorded as failing, and records a failure on open() error.
 *
 * Returns the file descriptor on success or -1 on failure. */
int msr_open_cpu(const char *cpu, int flags);

/* Read 8 bytes at the given MSR offset. Returns 0 on success, -1 on error
 * (errno preserved from pread). */
int msr_read_u64(int fd, unsigned int offset, uint64_t *val);

/* Write 8 bytes at the given MSR offset. Returns 0 on success, -1 on error
 * (errno preserved from pwrite). */
int msr_write_u64(int fd, unsigned int offset, uint64_t val);

#endif
