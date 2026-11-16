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

struct stats_type host_mem_job_stats_type = {
    .st_name = "host_mem_job",
    .st_collect = &host_mem_job_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
