/* Per-type schema, device instances, and metric get/set helpers. */
#ifndef _STATS_H_
#define _STATS_H_
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "cpuid.h"
#include "dict.h"
#include "JOIN.h"
#include "schema.h"
#include "trace.h"

#define SCHEMA_DEF(k,o,d,r...) " " #k "," o

/* Fixed size avoids a trailing flexible array in static struct stats_type objects.
 * NVC++/LLVM mis-sized st_name[] for some names (e.g. host_sysv_shm); longest current
 * emitted name is host_cpu_hw (12 chars). */
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
  unsigned int st_schema_def_owned:1;
  struct schema st_schema;
  struct dict st_current_dict;
  unsigned int st_enabled:1, st_selected:1;
  char st_name[STATS_TYPE_NAME_MAX];
};

struct stats {
  struct stats_type *s_type;
  unsigned long long *s_val;
  char s_dev[];
};

static inline struct stats *key_to_stats(const char *key)
{
  size_t s_dev_offset = ((struct stats *) NULL)->s_dev - (char *) NULL;
  return (struct stats *) (key - s_dev_offset);
}

int stats_type_init(struct stats_type *type);
void stats_type_destroy(struct stats_type *type);
struct stats_type *stats_type_for_each(size_t *i);
struct stats_type *stats_type_get(const char *name);

struct stats *get_current_stats(struct stats_type *type, const char *dev);
void stats_set(struct stats *stats, const char *key, unsigned long long val);
void stats_inc(struct stats *stats, const char *key, unsigned long long val);

#endif
