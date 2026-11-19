/*! \file x86_rapl.h
 *  x86 RAPL energy per socket: the perf `power` PMU (energy-* events, scaled by their .scale),
 *  with powercap energy_uj per domain as fallback. No MSR access.
 *
 *  Each reading is a running total in millijoules kept by the monitor since begin, so the
 *  consumer's delta / time is watts. Totals are published modulo 2^X86_RAPL_WIRE_BITS because the
 *  intel_x86_rapl / amd_x86_rapl keys are declared W=32 and the consumer unwraps at that width.
 */

#ifndef X86_RAPL_H
#define X86_RAPL_H

#include <stdint.h>

#include "perf_util.h"
#include "rapl_powercap.h"
#include "stats_sentinel.h"

#define X86_RAPL_SYSFS_ROOT "/sys"
#define X86_RAPL_WIRE_BITS 32

/* RAPL domains: perf event energy-pkg / energy-cores / energy-gpu / energy-ram; powercap zone
 * package-N / core (cores) / uncore / dram (ram). */
enum x86_rapl_domain { X86_RAPL_PKG, X86_RAPL_CORES, X86_RAPL_GPU, X86_RAPL_RAM, X86_RAPL_DOMAINS };

#define X86_RAPL_BIT(d) (1u << (d))

enum x86_rapl_source { X86_RAPL_SRC_NONE, X86_RAPL_SRC_PERF, X86_RAPL_SRC_POWERCAP };

/* One domain of one socket. */
struct x86_rapl_counter {
  enum x86_rapl_source source;
  enum stats_sentinel absent; /* emitted every collect while source is NONE */
  int fd;                     /* perf fd, -1 unless source is PERF */
  struct perf_prev prev;
  double mj_per_count; /* perf .scale (Joules) x 1000 */
  struct rapl_powercap_counter pc;
  double total_mj; /* running total since begin */
};

struct x86_rapl_socket {
  int id;  /* physical_package_id: the device name */
  int cpu; /* CPU the perf events are opened on */
  struct x86_rapl_counter dom[X86_RAPL_DOMAINS];
};

struct x86_rapl {
  struct x86_rapl_socket *sockets;
  int n_sockets;
};

/* One published reading: mj valid only when status is STATS_SENTINEL_OK. */
struct x86_rapl_sample {
  uint64_t mj; /* running total modulo 2^X86_RAPL_WIRE_BITS */
  enum stats_sentinel status;
};

/**
 * Discovers sockets under <sysfs_root>/devices/system/cpu and opens every domain in @p domains
 * (X86_RAPL_BIT mask) per socket: the power PMU event on the PMU cpumask CPU of that socket, else
 * the socket's powercap zone. Closes any state from an earlier begin first.
 * @return number of sockets with at least one readable domain (0: nothing to publish).
 */
int x86_rapl_begin(struct x86_rapl *r, const char *sysfs_root, unsigned domains,
                   perf_open_fn open_fn);

/** One bounded read per open domain of socket @p idx; fills out[X86_RAPL_DOMAINS]. */
void x86_rapl_read(struct x86_rapl *r, int idx, struct x86_rapl_sample out[X86_RAPL_DOMAINS]);

/** Closes every fd and frees the socket table. */
void x86_rapl_end(struct x86_rapl *r);

#endif
