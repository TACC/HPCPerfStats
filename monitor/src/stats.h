/* Per-type schema, device instances, and metric get/set helpers. */
#ifndef _STATS_H_
#define _STATS_H_
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "cpuid.h"
#include "dict.h"
#include "JOIN.h"
#include "schema.h"
#include "stats_sentinel.h"
#include "stats_sentinel_debug.h"
#include "trace.h"

#define SCHEMA_DEF(k, o, d, r...) " " #k "," o

/* Fixed size avoids a trailing flexible array in static struct stats_type objects:
 * NVC++/LLVM mis-sized st_name[] for some names (e.g. host_sysv_shm). */
#define STATS_TYPE_NAME_MAX 40

extern double current_time;
extern char jobid[80];
extern int nr_cpus;
extern int n_pmcs;
extern processor_t processor;
/* Set by daemon collection path for samples that coincide with `$` schema/header changeover. */
extern int stats_collect_on_changeover;

struct stats_type {
  int (*st_begin)(struct stats_type *type);
  void (*st_collect)(struct stats_type *type);
  char *st_schema_def;
  unsigned int st_schema_def_owned : 1;
  struct schema st_schema;
  struct dict st_current_dict;
  /* st_prune_untouched: rows are transient entities (processes), dropped after a cycle that
   * touched none of their keys. Never set for real devices, whose untouched keys emit -1. */
  unsigned int st_enabled : 1, st_selected : 1, st_prune_untouched : 1;
  char st_name[STATS_TYPE_NAME_MAX];
};

struct stats {
  struct stats_type *s_type;
  unsigned long long *s_val;
  /* Per-schema-index enum stats_sentinel: STATS_SENTINEL_OK when s_val holds this
   * cycle's reading, else the code the row prints as "-<n>" instead of s_val. */
  unsigned char *s_val_state;
#ifdef DEBUG
  struct stats_sentinel_prov *s_val_prov; /* per schema index; NULL for rows not from stats.c */
#endif
  char s_dev[];
};

static inline struct stats *key_to_stats(const char *key)
{
  /* offsetof, not NULL-pointer subtraction (undefined behavior). */
  return (struct stats *)(key - offsetof(struct stats, s_dev));
}

int stats_type_init(struct stats_type *type);
void stats_type_destroy(struct stats_type *type);
struct stats_type *stats_type_for_each(size_t *i);
struct stats_type *stats_type_get(const char *name);

struct stats *get_current_stats(struct stats_type *type, const char *dev);
void stats_set(struct stats *stats, const char *key, unsigned long long val);
void stats_inc(struct stats *stats, const char *key, unsigned long long val);
/* Adds one source's reading to a row summed over several sources (OSCs of one mount): stores
 * `val` while `key` is still NOT_UPDATED this cycle, adds it once a source set the key, and leaves
 * a code from a failed source in place so a partial sum is never published. */
void stats_accumulate(struct stats *stats, const char *key, unsigned long long val);
/* Mark `key` unreadable this cycle with `code`; s_val keeps its last value. Tier-gated
 * like stats_set. */
void stats_set_missing(struct stats *stats, const char *key, enum stats_sentinel code);
/* Sentinel for a failed open/read errno: absent source NOT_PRESENT, denied PERMISSION_DENIED,
 * busy PMU_BUSY, anything else READ_FAILED. */
static inline enum stats_sentinel stats_sentinel_for_errno(int err)
{
  switch (err) {
  case ENOENT:
  case ENODEV:
  case ENXIO:
  case EOPNOTSUPP:
    return STATS_SENTINEL_NOT_PRESENT;
  case EACCES:
  case EPERM:
    return STATS_SENTINEL_PERMISSION_DENIED;
  case EBUSY:
    return STATS_SENTINEL_PMU_BUSY;
  default:
    return STATS_SENTINEL_READ_FAILED;
  }
}

/* stats_set_missing for every key active in the current collect phase that no collector has
 * set or marked yet this cycle (state still STATS_SENTINEL_NOT_UPDATED), so a whole-device or
 * whole-file failure never overwrites a value or a more specific code from another source. */
void stats_set_missing_all(struct stats *stats, enum stats_sentinel code);
/* stats_set_missing_all on every existing device row of `type` (a collector that skips the
 * whole type this cycle, e.g. a cooldown). */
void stats_type_set_missing_all(struct stats_type *type, enum stats_sentinel code);
/* Per-cycle reset: every key of every `type` device that is active in the current
 * phase becomes STATS_SENTINEL_NOT_UPDATED until a collector sets it. */
void stats_type_mark_not_updated(struct stats_type *type);
/* After collect: when `type` has st_prune_untouched, free every row whose active keys are all
 * still NOT_UPDATED (the entity is gone; it gets a fresh row if it returns). */
void stats_type_prune_untouched(struct stats_type *type);

#ifdef DEBUG
/* DEBUG: each call site registers in the site registry and records its key's provenance
 * (stats_sentinel_debug.h) before the real setter runs; definitions use (name)(...) syntax. */
static inline void stats_set_missing_at(struct stats_sentinel_site *site, struct stats *stats,
                                        const char *key, enum stats_sentinel code)
{
  int err = errno;

  site->hits++;
  if (stats_sentinel_debug_note != NULL)
    stats_sentinel_debug_note(site, stats, key, err);
  (stats_set_missing)(stats, key, code);
  errno = err;
}

static inline void stats_set_missing_all_at(struct stats_sentinel_site *site, struct stats *stats,
                                            enum stats_sentinel code)
{
  int err = errno;

  site->hits++;
  if (stats_sentinel_debug_note_all != NULL)
    stats_sentinel_debug_note_all(site, stats, err);
  (stats_set_missing_all)(stats, code);
  errno = err;
}

static inline void stats_type_set_missing_all_at(struct stats_sentinel_site *site,
                                                 struct stats_type *type, enum stats_sentinel code)
{
  int err = errno;

  site->hits++;
  if (stats_sentinel_debug_note_type != NULL)
    stats_sentinel_debug_note_type(site, type, err);
  (stats_type_set_missing_all)(type, code);
  errno = err;
}

#define stats_set_missing(stats, key, code)                                                        \
  stats_set_missing_at(STATS_SENTINEL_SITE(), (stats), (key), (code))
#define stats_set_missing_all(stats, code)                                                         \
  stats_set_missing_all_at(STATS_SENTINEL_SITE(), (stats), (code))
#define stats_type_set_missing_all(type, code)                                                     \
  stats_type_set_missing_all_at(STATS_SENTINEL_SITE(), (type), (code))
#endif

#endif
