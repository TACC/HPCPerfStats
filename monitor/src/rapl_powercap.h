/*! \file rapl_powercap.h
 *  RAPL energy zones under /sys/class/powercap (energy_uj; no MSR). Fallback source of x86_rapl.
 */

#ifndef _RAPL_POWERCAP_H_
#define _RAPL_POWERCAP_H_

#include <stddef.h>
#include <stdint.h>

/* One open energy_uj counter. energy_uj counts up to max_energy_range_uj, then restarts at 0. */
struct rapl_powercap_counter {
  int fd;          /* energy_uj, kept open and re-read with pread */
  uint64_t max_uj; /* max_energy_range_uj; 0 when unreadable (a drop cannot be unwrapped) */
  uint64_t prev_uj;
};

/* Parse powercap domain name "package-N" into its socket id; -1 if not a package name. */
int rapl_powercap_parse_package_id(const char *name, unsigned *socket_out);

/*
 * Zone directory of socket_id under powercap_root (intel-rapl / amd-rapl). child_names NULL picks
 * the package zone itself; otherwise the package's child zone whose name equals one entry of the
 * NULL-terminated child_names. Returns 0 with the path in out, -ENOENT when there is no such zone.
 */
int rapl_powercap_find_zone(const char *powercap_root, unsigned socket_id,
                            const char *const *child_names, char *out, size_t cap);

/*
 * Delta from prev_uj to cur_uj on a counter that wraps after max_uj (one wrap at most).
 * Returns 0, or -ERANGE when cur_uj < prev_uj and max_uj cannot explain it (unknown or too small).
 */
int rapl_powercap_wrap_delta(uint64_t prev_uj, uint64_t cur_uj, uint64_t max_uj,
                             uint64_t *delta_uj);

/* Opens <zone_dir>/energy_uj, reads max_energy_range_uj and seeds the counter with one read.
 * Returns 0, or -errno (-EACCES / -EPERM: root-only energy_uj since the PLATYPUS mitigation). */
int rapl_powercap_counter_open(struct rapl_powercap_counter *c, const char *zone_dir);

/*
 * Re-reads energy_uj: *delta_uj is the energy since the previous good read, wrap included.
 * Returns 0; -ERANGE when the counter dropped without a usable wrap range (re-seeded, interval
 * lost); -errno on a failed read (previous reading kept, so the next good read spans the gap).
 */
int rapl_powercap_counter_read(struct rapl_powercap_counter *c, uint64_t *delta_uj);

/* Closes the energy_uj fd (no-op on a counter that never opened: fd -1). */
void rapl_powercap_counter_close(struct rapl_powercap_counter *c);

#endif
