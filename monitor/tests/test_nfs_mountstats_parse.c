/* host_nfs mountstats parse against a fixture with nfs (v3) and nfs4 mounts on tcp, udp and rdma
 * transports, a truncated mount and non-NFS mounts: rows, events delay, xprt and per-op field
 * offsets by kernel field index. Real nfs.c, collect.c and stats.c. */
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

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
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

/* delay is events field 24 (after inoderevalidate ... shortwrite). */
static void test_events_delay_field(void)
{
  assert(val_of(find_row("/mnt/v3"), "delay") == 2500);
  assert(val_of(find_row("/mnt/v4"), "delay") == 4400);
  assert(val_of(find_row("/mnt/udp"), "delay") == 6600);
}

/* bad_xids / req_u / bklog_u follow sends and recvs: field 7 on tcp and rdma (port bind_count
 * connect_count connect_time idle_time sends recvs), field 4 on udp (port bind_count sends recvs). */
static void test_xprt_fields_per_transport(void)
{
  struct stats *tcp = find_row("/mnt/v3");
  struct stats *rdma = find_row("/mnt/v4");
  struct stats *udp = find_row("/mnt/udp");

  assert(val_of(tcp, "xprt_bad_xids") == 7);
  assert(val_of(tcp, "xprt_req_u") == 8000);
  assert(val_of(tcp, "xprt_bklog_u") == 9000);
  assert(val_of(rdma, "xprt_bad_xids") == 17);
  assert(val_of(rdma, "xprt_req_u") == 18000);
  assert(val_of(rdma, "xprt_bklog_u") == 19000);
  assert(val_of(udp, "xprt_bad_xids") == 27);
  assert(val_of(udp, "xprt_req_u") == 28000);
  assert(val_of(udp, "xprt_bklog_u") == 29000);
}

/* Per-op line: ops ntrans timeouts bytes_sent bytes_recv queue rtt execute [errors]. */
static void test_op_fields(void)
{
  struct stats *v3 = find_row("/mnt/v3");
  struct stats *udp = find_row("/mnt/udp");

  assert(val_of(v3, "read_ops") == 50);
  assert(val_of(v3, "read_timeouts") == 2);
  assert(val_of(v3, "read_queue") == 11);
  assert(val_of(v3, "read_rtt") == 22);
  assert(val_of(v3, "write_ops") == 60);
  assert(val_of(v3, "write_rtt") == 23);
  assert(val_of(find_row("/mnt/v4"), "read_ops") == 70);
  assert(val_of(udp, "read_ops") == 90);
  assert(val_of(udp, "write_ops") == 92);
}

/* Truncated events / xprt / op lines set nothing rather than shifted values. */
static void test_truncated_lines_set_nothing(void)
{
  struct stats *s = find_row("/mnt/short");

  assert(val_of(s, "normal_read") == 3100);
  assert(state_of(s, "delay") == STATS_SENTINEL_NOT_UPDATED);
  assert(state_of(s, "xprt_bad_xids") == STATS_SENTINEL_NOT_UPDATED);
  assert(state_of(s, "xprt_req_u") == STATS_SENTINEL_NOT_UPDATED);
  assert(state_of(s, "read_ops") == STATS_SENTINEL_NOT_UPDATED);
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
  test_events_delay_field();
  test_xprt_fields_per_transport();
  test_op_fields();
  test_truncated_lines_set_nothing();

  stats_type_destroy(&nfs_stats_type);
  printf("test_nfs_mountstats_parse passed\n");
  return 0;
}
