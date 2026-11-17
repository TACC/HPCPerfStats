/* host_mem_job — per-jid cgroup memory; devices filled from job_shared_emit. */
#include "dict.h"
#include "host_mem_job.h"
#include "stats.h"

void key_stats_destroy(void *key);

static void host_mem_job_collect(struct stats_type *type)
{
  /* Clear prior sample devices; job_shared_emit repopulates after collect. */
  if (type == NULL)
    return;
  dict_destroy(&type->st_current_dict, &key_stats_destroy);
  (void)dict_init(&type->st_current_dict, 0);
}

void host_mem_job_publish(struct stats *st, const struct job_cgroup_mem_kb *kb)
{
  stats_set(st, "mem_used", kb->mem_used);
  if (kb->has_peak)
    stats_set(st, "mem_peak", kb->mem_peak);
  stats_set(st, "anon_pages", kb->anon_pages);
  stats_set(st, "file_pages", kb->file_pages);
  stats_set(st, "mapped", kb->mapped);
  stats_set(st, "dirty", kb->dirty);
  stats_set(st, "writeback", kb->writeback);
  stats_set(st, "page_tables", kb->page_tables);
  /* No memory.peak in this scope: mem_peak is absent, not a real 0. */
  stats_set_missing_all(st, STATS_SENTINEL_NOT_PRESENT);
}

struct stats_type host_mem_job_stats_type = {
    .st_name = "host_mem_job",
    .st_collect = &host_mem_job_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
