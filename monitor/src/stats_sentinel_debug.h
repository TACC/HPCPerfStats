/* DEBUG-only value sentinel tooling (--enable-debug; release builds compile none of it):
 *
 * - every stats_set_missing* call site registers itself in the "stats_sentinel_sites" linker
 * section (file, line, hit count), so a fault-injection run can report sites never exercised;
 * - each key records the site, errno and run length of its current sentinel (provenance), shown
 * in the /dev/shm "sentinels" file, and each state transition is logged once;
 * - STATS_FAULT(point, subject, &err) lets the debug_fault_inject conf key force a failure at a
 * fault point for N collect cycles, optionally after D clean ones: open, opendir (I/O
 * primitives), perf_open (subject: event name), mad_query (HCA), dcgm_fetch, hwmon_read (meter
 * name: CPU, Grace, SysIO, Module), scandir (host_proc), statfs (host_tmpfs mount dir),
 * net_rebuild_slow (host_net).
 *
 * Collector code uses none of this directly: stats.h maps stats_set_missing* onto the *_at
 * wrappers. The recording functions are weak, so units linked without stats_sentinel_debug.c
 * (unit tests) still build and the wrappers only count hits. */
#ifndef STATS_SENTINEL_DEBUG_H_
#define STATS_SENTINEL_DEBUG_H_

#ifdef DEBUG
#include <stddef.h>
#include <stdio.h>
#include <time.h>

#include "stats_sentinel.h"

struct stats;
struct stats_type;

struct stats_sentinel_site {
  const char *file;
  int line;
  unsigned long hits;
};

/* Provenance of one key's current state. */
struct stats_sentinel_prov {
  const struct stats_sentinel_site *site; /* NULL: no collector marked it this cycle */
  int err;                                /* errno at the site */
  unsigned char noted;                    /* a site marked the key this cycle */
  unsigned char prev;                     /* state at the previous cycle end */
  time_t first;                           /* cycle that started the current non-OK run */
  time_t last;
  unsigned long count; /* cycles in the current non-OK run */
};

/* The registry holds pointers (uniform stride) to one static site per expansion. */
#define STATS_SENTINEL_SITE()                                                                      \
  __extension__({                                                                                  \
    static struct stats_sentinel_site stats_sentinel_site_ = {__FILE__, __LINE__, 0};              \
    static struct stats_sentinel_site *stats_sentinel_site_ref_                                    \
        __attribute__((used, section("stats_sentinel_sites"))) = &stats_sentinel_site_;            \
    (void)stats_sentinel_site_ref_;                                                                \
    &stats_sentinel_site_;                                                                         \
  })

void stats_sentinel_debug_note(const struct stats_sentinel_site *site, struct stats *stats,
                               const char *key, int err) __attribute__((weak));
void stats_sentinel_debug_note_all(const struct stats_sentinel_site *site, struct stats *stats,
                                   int err) __attribute__((weak));
void stats_sentinel_debug_note_type(const struct stats_sentinel_site *site, struct stats_type *type,
                                    int err) __attribute__((weak));
int stats_fault_forced(const char *point, const char *subject, int *err) __attribute__((weak));

/* Nonzero (and *err set) when debug_fault_inject forces a failure of `point` on `subject`. */
#define STATS_FAULT(point, subject, err)                                                           \
  (stats_fault_forced != NULL && stats_fault_forced((point), (subject), (err)))

/* After a type's collect: provenance run lengths and one log line per state transition. */
void stats_sentinel_debug_cycle_end(struct stats_type *type, time_t now) __attribute__((weak));
/* Advance fault-injection tick counters once per collect cycle. */
void stats_fault_inject_tick(void) __attribute__((weak));

/* "point[:subject][!errno][@[delay+]ticks],..." (subject: path substring; errno default EIO;
 * ticks: collect cycles, default until cleared; delay: collect cycles that run unfaulted first,
 * so rows exist before the fault fires). Replaces the table; NULL or "" clears it. Returns the
 * entry count, or -1 on a malformed spec (the table is then empty). */
int stats_fault_inject_set(const char *spec) __attribute__((weak));
/* Times entry `i` of the table forced a failure (0 for a bad index). */
unsigned long stats_fault_inject_fired(int i);

/* One line per key whose state is not OK:
 * "type dev key code slug errno site first_ts last_ts count" (site "-" when none). Returns a
 * malloc'd NUL-terminated buffer (caller frees) or NULL. */
char *stats_sentinel_debug_render(size_t *len_out);
/* One line per registered site: "file:line hits". Returns the number of sites never hit. */
size_t stats_sentinel_sites_report(FILE *out);
/* Write the "sentinels" and "sentinel_sites" files to the DEBUG shm directory. */
void stats_sentinel_debug_write_shm(void);
#else
#define STATS_FAULT(point, subject, err) 0
#endif

#endif
