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

/* Set a memory.stat key only when it was parsed; an unreadable memory.stat maps its errno. */
static void publish_stat_key(struct stats *st, const struct job_cgroup_mem_kb *kb, unsigned int bit,
                             const char *key, unsigned long long val)
{
  if (kb->stat_present & bit)
    stats_set(st, key, val);
  else if (kb->stat_errno != 0)
    stats_set_missing(st, key, stats_sentinel_for_errno(kb->stat_errno));
}

void host_mem_job_publish(struct stats *st, const struct job_cgroup_mem_kb *kb)
{
  stats_set(st, "mem_used", kb->mem_used);
  if (kb->has_peak)
    stats_set(st, "mem_peak", kb->mem_peak);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_ANON, "anon_pages", kb->anon_pages);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_FILE, "file_pages", kb->file_pages);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_MAPPED, "mapped", kb->mapped);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_DIRTY, "dirty", kb->dirty);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_WRITEBACK, "writeback", kb->writeback);
  publish_stat_key(st, kb, JOB_CGROUP_MEM_STAT_PAGE_TABLES, "page_tables", kb->page_tables);
  /* mem_peak without memory.peak and stat keys memory.stat does not list are absent, not 0. */
  stats_set_missing_all(st, STATS_SENTINEL_NOT_PRESENT);
}

struct stats_type host_mem_job_stats_type = {
    .st_name = "host_mem_job",
    .st_collect = &host_mem_job_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
