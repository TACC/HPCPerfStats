/* host_nfs mountstats parse against a fixture with nfs (v3) and nfs4 mounts on tcp, udp and rdma
 * transports, a truncated mount and non-NFS mounts. Real nfs.c, collect.c and stats.c. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "host_nfs.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
extern struct stats_type nfs_stats_type;

static struct stats *find_row(const char *dev)
{
  size_t i = 0;
  char *key;

  while ((key = dict_for_each(&nfs_stats_type.st_current_dict, &i)) != NULL)
    if (strcmp(key, dev) == 0)
      return key_to_stats(key);
  return NULL;
}

static size_t row_count(void)
{
  size_t i = 0;
  size_t n = 0;

  while (dict_for_each(&nfs_stats_type.st_current_dict, &i) != NULL)
    n++;
  return n;
}

static unsigned long long val_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  assert(s->s_val_state[i] == STATS_SENTINEL_OK);
  return s->s_val[i];
}

/* One row per nfs and nfs4 mount point; rootfs, proc, nfsd and an unknown nfs3x fstype skipped. */
static void test_rows_for_nfs_and_nfs4(void)
{
  assert(find_row("/mnt/v3") != NULL);
  assert(find_row("/mnt/v4") != NULL);
  assert(find_row("/mnt/udp") != NULL);
  assert(find_row("/mnt/short") != NULL);
  assert(find_row("/mnt/n3x") == NULL);
  assert(find_row("/proc") == NULL);
  assert(find_row("/proc/fs/nfsd") == NULL);
  assert(row_count() == 4);

  assert(val_of(find_row("/mnt/v3"), "normal_read") == 100);
  assert(val_of(find_row("/mnt/v4"), "normal_read") == 1100);
  assert(val_of(find_row("/mnt/v4"), "server_write") == 1600);
}

int main(void)
{
  assert(stats_type_init(&nfs_stats_type) == 0);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  nfs_test_set_mountstats_path(NFS_MOUNTSTATS_FIXTURE);
  stats_type_mark_not_updated(&nfs_stats_type);
  nfs_stats_type.st_collect(&nfs_stats_type);
  nfs_test_set_mountstats_path(NULL);

  test_rows_for_nfs_and_nfs4();

  stats_type_destroy(&nfs_stats_type);
  printf("test_nfs_mountstats_parse passed\n");
  return 0;
}
