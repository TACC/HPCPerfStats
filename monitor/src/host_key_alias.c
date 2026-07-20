/* Map /proc field names to emitted snake_case stat keys (mem, proc). */
#include <string.h>
#include "host_key_alias.h"
#include "stats.h"

struct host_key_alias_entry {
  const char *kernel_key;
  const char *emit_key;
};

static const struct host_key_alias_entry g_mem_aliases[] = {
    {"MemTotal", "mem_total"},
    {"MemFree", "mem_free"},
    {"MemUsed", "mem_used"},
    {"Active", "active"},
    {"Inactive", "inactive"},
    {"Dirty", "dirty"},
    {"Writeback", "writeback"},
    {"FilePages", "file_pages"},
    {"Mapped", "mapped"},
    {"AnonPages", "anon_pages"},
    {"PageTables", "page_tables"},
    {"NFS_Unstable", "nfs_unstable"},
    {"Bounce", "bounce"},
    {"Slab", "slab"},
    {"AnonHugePages", "anon_huge_pages"},
    {"HugePages_Total", "huge_pages_total"},
    {"HugePages_Free", "huge_pages_free"},
    {NULL, NULL},
};

static const struct host_key_alias_entry g_proc_aliases[] = {
    {"Uid", "uid"},         {"VmPeak", "vm_peak"}, {"VmSize", "vm_size"}, {"VmLck", "vm_lck"},
    {"VmHWM", "vm_hwm"},    {"VmRSS", "vm_rss"},   {"VmData", "vm_data"}, {"VmStk", "vm_stk"},
    {"VmExe", "vm_exe"},    {"VmLib", "vm_lib"},   {"VmPTE", "vm_pte"},   {"VmSwap", "vm_swap"},
    {"Threads", "threads"}, {NULL, NULL},
};

static const char *lookup_table(const struct host_key_alias_entry *table, const char *kernel_key)
{
  size_t i;

  if (kernel_key == NULL || kernel_key[0] == '\0')
    return NULL;
  for (i = 0; table[i].kernel_key != NULL; i++) {
    if (strcmp(kernel_key, table[i].kernel_key) == 0)
      return table[i].emit_key;
  }
  return NULL;
}

static const char *host_mem_key_alias(const char *kernel_key)
{
  return lookup_table(g_mem_aliases, kernel_key);
}

static const char *host_proc_key_alias(const char *kernel_key)
{
  return lookup_table(g_proc_aliases, kernel_key);
}

const char *host_key_alias_lookup(const char *kernel_key)
{
  const char *emit;

  emit = host_mem_key_alias(kernel_key);
  if (emit != NULL)
    return emit;
  return host_proc_key_alias(kernel_key);
}

void host_key_alias_emit(struct stats *stats, const char *kernel_key, unsigned long long val)
{
  const char *emit;

  if (stats == NULL)
    return;
  emit = host_key_alias_lookup(kernel_key);
  if (emit == NULL)
    return;
  stats_set(stats, emit, val);
}
