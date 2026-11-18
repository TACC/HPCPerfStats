/* beegfs_client I/O: totals accumulate across refreshes and failures, a gated tick re-publishes
 * the last totals or code, and the collect path maps each mount to its client instance by
 * cfgFile, reading real bytes over TCP and NOT_PRESENT when TCP cannot see all of the traffic
 * (RDMA, fewer matched sockets than listed connections, no matching instance). */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include "beegfs_client.h"
#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"
#include "test_rm_tree.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
double sample_freq = 300.0;
extern struct stats_type beegfs_stats_type;

static char g_dir[] = "/tmp/hps_beegfs_ioXXXXXX";

static unsigned int state(struct stats *row, const char *key)
{
  int i = schema_ref(&row->s_type->st_schema, key);

  assert(i >= 0);
  return row->s_val_state[i];
}

static unsigned long long val(struct stats *row, const char *key)
{
  int i = schema_ref(&row->s_type->st_schema, key);

  assert(i >= 0 && row->s_val_state[i] == STATS_SENTINEL_OK);
  return row->s_val[i];
}

static void write_file(const char *rel, const char *content)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", g_dir, rel);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(content, f);
  fclose(f);
}

static void make_dir(const char *rel)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/%s", g_dir, rel);
  assert(mkdir(path, 0700) == 0);
}

static void test_publish_and_republish(void)
{
  struct beegfs_tcp_sock s = {.cookie = 1, .acked = 100, .received = 10};
  struct stats *row;

  assert(stats_type_init(&beegfs_stats_type) == 0);
  row = get_current_stats(&beegfs_stats_type, "/beegfs");

  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_io(row, &s, 1, STATS_SENTINEL_OK);
  assert(val(row, "vfs_write_bytes") == 100ULL && val(row, "vfs_read_bytes") == 10ULL);
  assert(state(row, "vfs_read_ops") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "vfs_statfs_ops") == STATS_SENTINEL_NOT_PRESENT);

  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_io(row);
  assert(val(row, "vfs_write_bytes") == 100ULL && val(row, "vfs_read_bytes") == 10ULL);
  assert(state(row, "vfs_open_ops") == STATS_SENTINEL_NOT_PRESENT);

  /* A failed refresh publishes its code, also on the gated tick after it. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_io(row, NULL, 0, STATS_SENTINEL_READ_FAILED);
  assert(state(row, "vfs_write_bytes") == STATS_SENTINEL_READ_FAILED);
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_io(row);
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_READ_FAILED);
  assert(state(row, "fs_bytes_total") == STATS_SENTINEL_NOT_UPDATED);

  /* The totals survive the failure and keep growing from the last good read. */
  s.acked = 150;
  s.received = 30;
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_publish_io(row, &s, 1, STATS_SENTINEL_OK);
  assert(val(row, "vfs_write_bytes") == 150ULL && val(row, "vfs_read_bytes") == 30ULL);

  /* A mount never refreshed has nothing cached: its keys stay NOT_UPDATED. */
  row = get_current_stats(&beegfs_stats_type, "/beegfs2");
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_republish_io(row);
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_NOT_UPDATED);
  beegfs_publish_io(NULL, &s, 1, STATS_SENTINEL_OK);
  beegfs_republish_io(NULL);
  stats_type_destroy(&beegfs_stats_type);
}

/* A loopback listener stands in for a storage server; the client side has sent the returned
 * byte count. fds: listener, client, accepted. */
static int open_loopback_pair(int fds[3], char *endpoint, size_t endpoint_sz)
{
  static char payload[50000];
  char sink[65536];
  struct sockaddr_in sa = {.sin_family = AF_INET};
  socklen_t sl = sizeof(sa);
  size_t got = 0;

  sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  fds[0] = socket(AF_INET, SOCK_STREAM, 0);
  assert(fds[0] >= 0 && bind(fds[0], (struct sockaddr *)&sa, sizeof(sa)) == 0);
  assert(listen(fds[0], 1) == 0 && getsockname(fds[0], (struct sockaddr *)&sa, &sl) == 0);
  fds[1] = socket(AF_INET, SOCK_STREAM, 0);
  assert(fds[1] >= 0 && connect(fds[1], (struct sockaddr *)&sa, sizeof(sa)) == 0);
  fds[2] = accept(fds[0], NULL, NULL);
  assert(fds[2] >= 0);
  assert(send(fds[1], payload, sizeof(payload), 0) == (ssize_t)sizeof(payload));
  while (got < sizeof(payload)) {
    ssize_t r = recv(fds[2], sink, sizeof(sink), 0);

    assert(r > 0);
    got += (size_t)r;
  }
  snprintf(endpoint, endpoint_sz, "127.0.0.1:%u", (unsigned)ntohs(sa.sin_port));
  return (int)sizeof(payload);
}

/* 1 when the kernel answers NETLINK_SOCK_DIAG; qemu-user (and a kernel without sock_diag) does
 * not, and then every TCP-readable mount publishes NOT_PRESENT. */
static int have_sock_diag(void)
{
  struct beegfs_tcp_peer peer = {.addr = htonl(INADDR_LOOPBACK), .port = 1};
  struct beegfs_tcp_sock sock;
  size_t n;

  if (beegfs_tcp_dump(&peer, 1, &sock, 1, &n) == 0)
    return 1;
  assert(errno == EOPNOTSUPP);
  printf("test_beegfs_io: no NETLINK_SOCK_DIAG (emulated?), live mounts expect NOT_PRESENT\n");
  return 0;
}

static void test_collect_maps_mounts_to_instances(void)
{
  static char mounts[512];
  static char procfs[512];
  char endpoint[32];
  char text[256];
  int fds[3];
  int sent = open_loopback_pair(fds, endpoint, sizeof(endpoint));
  int live = have_sock_diag();
  struct stats *row;

  make_dir("proc");
  make_dir("proc/c1");
  make_dir("proc/c2");
  make_dir("proc/c3");
  write_file("proc/c1/config", "cfgFile = /etc/beegfs/a.conf\nconnUseRDMA = false\n");
  snprintf(text, sizeof(text), "st1 [ID: 1]\n   Connections: TCP: 1 (%s); \n", endpoint);
  write_file("proc/c1/storage_nodes", text);
  write_file("proc/c2/config", "cfgFile = /etc/beegfs/b.conf\n");
  write_file("proc/c2/storage_nodes",
             "st1 [ID: 1]\n   Connections: RDMA: 4 (192.168.40.14:8201); \n");
  write_file("proc/c3/config", "cfgFile = /etc/beegfs/d.conf\n");
  snprintf(text, sizeof(text), "st1 [ID: 1]\n   Connections: TCP: 2 (%s); \n", endpoint);
  write_file("proc/c3/storage_nodes", text);
  write_file("mounts", "beegfs_nodev /mnt/a beegfs rw,cfgFile=/etc/beegfs/a.conf,_netdev 0 0\n"
                       "beegfs_nodev /mnt/d beegfs rw,cfgFile=/etc/beegfs/d.conf,_netdev 0 0\n"
                       "beegfs_nodev /mnt/b beegfs rw,cfgFile=/etc/beegfs/b.conf,_netdev 0 0\n"
                       "beegfs_nodev /mnt/c beegfs rw,cfgFile=/etc/beegfs/c.conf,_netdev 0 0\n"
                       "tmpfs /tmp tmpfs rw 0 0\n");
  snprintf(mounts, sizeof(mounts), "%s/mounts", g_dir);
  beegfs_test_set_mounts_path(mounts);
  snprintf(procfs, sizeof(procfs), "%s/proc", g_dir);
  beegfs_test_set_procfs_root(procfs);

  assert(stats_type_init(&beegfs_stats_type) == 0);
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_stats_type.st_collect(&beegfs_stats_type);
  row = get_current_stats(&beegfs_stats_type, "/mnt/a");
  assert(live ? val(row, "vfs_write_bytes") >= (unsigned long long)sent
              : state(row, "vfs_write_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "vfs_write_ops") == STATS_SENTINEL_NOT_PRESENT);
  row = get_current_stats(&beegfs_stats_type, "/mnt/b");
  assert(state(row, "vfs_write_bytes") == STATS_SENTINEL_NOT_PRESENT);
  row = get_current_stats(&beegfs_stats_type, "/mnt/c");
  assert(state(row, "vfs_read_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(row, "fs_bytes_total") == STATS_SENTINEL_NOT_PRESENT);
  row = get_current_stats(&beegfs_stats_type, "/mnt/d");
  assert(state(row, "vfs_write_bytes") == STATS_SENTINEL_NOT_PRESENT);

  /* The next tick is inside the refresh interval: the same totals and codes come back. */
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_stats_type.st_collect(&beegfs_stats_type);
  row = get_current_stats(&beegfs_stats_type, "/mnt/a");
  assert(live ? val(row, "vfs_write_bytes") >= (unsigned long long)sent
              : state(row, "vfs_write_bytes") == STATS_SENTINEL_NOT_PRESENT);
  row = get_current_stats(&beegfs_stats_type, "/mnt/b");
  assert(state(row, "vfs_write_bytes") == STATS_SENTINEL_NOT_PRESENT);

  stats_type_destroy(&beegfs_stats_type);
  beegfs_test_set_mounts_path(NULL);
  beegfs_test_set_procfs_root(NULL);
  for (int i = 0; i < 3; i++)
    close(fds[i]);
}

/* A mount without cfgFile= takes the only client instance; a listing whose nodes are all idle
 * (`<none>`) is a real zero, not a code; a slow-tier FULL tick refreshes inside the interval. */
static void test_collect_single_instance_idle(void)
{
  static char mounts[512];
  static char procfs[512];
  char text[600];

  make_dir("proc1");
  make_dir("proc1/only");
  write_file("proc1/only/config", "cfgFile = /etc/beegfs/beegfs-client.conf\n");
  write_file("proc1/only/storage_nodes", "st1 [ID: 1]\n   Connections: <none>\n");
  snprintf(text, sizeof(text), "beegfs_nodev %s beegfs rw,relatime 0 0\n", g_dir);
  write_file("mounts1", text);
  snprintf(mounts, sizeof(mounts), "%s/mounts1", g_dir);
  beegfs_test_set_mounts_path(mounts);
  snprintf(procfs, sizeof(procfs), "%s/proc1", g_dir);
  beegfs_test_set_procfs_root(procfs);
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FULL);

  assert(stats_type_init(&beegfs_stats_type) == 0);
  stats_type_mark_not_updated(&beegfs_stats_type);
  beegfs_stats_type.st_collect(&beegfs_stats_type);
  {
    struct stats *row = get_current_stats(&beegfs_stats_type, g_dir);

    assert(val(row, "vfs_write_bytes") == 0ULL && val(row, "vfs_read_bytes") == 0ULL);
    assert(val(row, "fs_bytes_total") > 0ULL);
  }

  stats_type_destroy(&beegfs_stats_type);
  collect_tier_set_enabled(0);
  beegfs_test_set_mounts_path(NULL);
  beegfs_test_set_procfs_root(NULL);
}

int main(void)
{
  assert(mkdtemp(g_dir) != NULL);
  test_publish_and_republish();
  test_collect_maps_mounts_to_instances();
  test_collect_single_instance_idle();
  assert(test_rm_tree(g_dir) == 0);
  printf("test_beegfs_io passed\n");
  return 0;
}
