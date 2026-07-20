/* opa_sysfs classic + hw_counters filename maps and collect fallback. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "opa_sysfs.h"
#include "stats.h"

#define MAX_REC 16

struct rec {
  char key[64];
  unsigned long long val;
};

static struct rec g_recs[MAX_REC];
static int g_nrec;

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  (void)stats;
  if (key == NULL || g_nrec >= MAX_REC)
    return;
  snprintf(g_recs[g_nrec].key, sizeof(g_recs[g_nrec].key), "%s", key);
  g_recs[g_nrec].val = val;
  g_nrec++;
}

static void reset_recs(void)
{
  g_nrec = 0;
  memset(g_recs, 0, sizeof(g_recs));
}

static unsigned long long find_rec(const char *key)
{
  int i;

  for (i = 0; i < g_nrec; i++) {
    if (strcmp(g_recs[i].key, key) == 0)
      return g_recs[i].val;
  }
  return (unsigned long long)-1;
}

static int has_rec(const char *key)
{
  int i;

  for (i = 0; i < g_nrec; i++) {
    if (strcmp(g_recs[i].key, key) == 0)
      return 1;
  }
  return 0;
}

static void test_classic_mapped_keys(void)
{
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_data"), "port_xmit_data") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_rcv_data"), "port_rcv_data") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_packets"), "port_xmit_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_rcv_packets"), "port_rcv_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("multicast_xmit_packets"),
                "port_multicast_xmit_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("multicast_rcv_packets"),
                "port_multicast_rcv_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_wait"), "port_xmit_wait") == 0);
}

static void test_classic_unmapped_and_null(void)
{
  assert(opa_sysfs_schema_key_for_file("port_rcv_fecn") == NULL);
  assert(opa_sysfs_schema_key_for_file("DmaWait") == NULL);
  assert(opa_sysfs_schema_key_for_file("TxWords") == NULL);
  assert(opa_sysfs_schema_key_for_file(NULL) == NULL);
  assert(opa_sysfs_schema_key_for_file("") == NULL);
}

static void test_hw_mapped_keys(void)
{
  assert(strcmp(opa_sysfs_hw_schema_key_for_file("TxWords"), "port_xmit_data") == 0);
  assert(strcmp(opa_sysfs_hw_schema_key_for_file("RxWords"), "port_rcv_data") == 0);
  assert(strcmp(opa_sysfs_hw_schema_key_for_file("TxPkt"), "port_xmit_pkts") == 0);
  assert(strcmp(opa_sysfs_hw_schema_key_for_file("RxPkt"), "port_rcv_pkts") == 0);
  assert(strcmp(opa_sysfs_hw_schema_key_for_file("TxWait"), "port_xmit_wait") == 0);
}

static void test_hw_unmapped_and_null(void)
{
  assert(opa_sysfs_hw_schema_key_for_file("DmaWait") == NULL);
  assert(opa_sysfs_hw_schema_key_for_file("port_xmit_data") == NULL);
  assert(opa_sysfs_hw_schema_key_for_file("RcvOverflow,32") == NULL);
  assert(opa_sysfs_hw_schema_key_for_file(NULL) == NULL);
  assert(opa_sysfs_hw_schema_key_for_file("") == NULL);
}

static void write_ull(const char *path, unsigned long long v)
{
  FILE *f = fopen(path, "w");

  assert(f != NULL);
  assert(fprintf(f, "%llu\n", v) > 0);
  assert(fclose(f) == 0);
}

static void mkdir_p(const char *path)
{
  assert(mkdir(path, 0755) == 0 || errno == EEXIST);
}

static char *make_fixture_root(void)
{
  const char *tmpdir = getenv("TMPDIR");
  char tmpl[256];
  char *root;

  if (tmpdir == NULL || tmpdir[0] == '\0')
    tmpdir = "/tmp";
  snprintf(tmpl, sizeof(tmpl), "%s/opa_sysfs_XXXXXX", tmpdir);
  root = mkdtemp(tmpl);
  assert(root != NULL);
  return strdup(root);
}

static void rm_tree(const char *root)
{
  char cmd[512];

  if (root == NULL)
    return;
  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
  (void)system(cmd);
}

static void test_collect_classic_success_skips_hw(void)
{
  char *root = make_fixture_root();
  char path[512];
  char buf[sizeof(struct stats) + 64];
  struct stats *stats = (struct stats *)buf;

  snprintf(path, sizeof(path), "%s/class", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/counters", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters", root);
  mkdir_p(path);

  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/counters/port_xmit_data", root);
  write_ull(path, 111ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/counters/port_rcv_data", root);
  write_ull(path, 222ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/TxWords", root);
  write_ull(path, 999999ULL);

  reset_recs();
  opa_sysfs_test_set_root(root);
  assert(opa_sysfs_collect_port(stats, "hfi1_0", 2) == 0);
  opa_sysfs_test_set_root(NULL);

  assert(has_rec("port_xmit_data"));
  assert(find_rec("port_xmit_data") == 111ULL);
  assert(find_rec("port_rcv_data") == 222ULL);
  /* Must not overwrite with hw when classic succeeded. */
  assert(find_rec("port_xmit_data") != 999999ULL);

  rm_tree(root);
  free(root);
}

static void test_collect_classic_miss_uses_hw(void)
{
  char *root = make_fixture_root();
  char path[512];
  char buf[sizeof(struct stats) + 64];
  struct stats *stats = (struct stats *)buf;

  snprintf(path, sizeof(path), "%s/class", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2", root);
  mkdir_p(path);
  /* Classic counters dir empty / missing files → n_ok==0 (CN5000 EINVAL case). */
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/counters", root);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters", root);
  mkdir_p(path);

  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/TxWords", root);
  write_ull(path, 1001ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/RxWords", root);
  write_ull(path, 1002ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/TxPkt", root);
  write_ull(path, 1003ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/RxPkt", root);
  write_ull(path, 1004ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/TxWait", root);
  write_ull(path, 1005ULL);
  snprintf(path, sizeof(path), "%s/class/infiniband/hfi1_0/ports/2/hw_counters/DmaWait", root);
  write_ull(path, 777ULL);

  reset_recs();
  opa_sysfs_test_set_root(root);
  assert(opa_sysfs_collect_port(stats, "hfi1_0", 2) == 0);
  opa_sysfs_test_set_root(NULL);

  assert(find_rec("port_xmit_data") == 1001ULL);
  assert(find_rec("port_rcv_data") == 1002ULL);
  assert(find_rec("port_xmit_pkts") == 1003ULL);
  assert(find_rec("port_rcv_pkts") == 1004ULL);
  assert(find_rec("port_xmit_wait") == 1005ULL);
  assert(!has_rec("DmaWait"));

  rm_tree(root);
  free(root);
}

static void test_collect_null_and_bad_port(void)
{
  char buf[sizeof(struct stats) + 64];
  struct stats *stats = (struct stats *)buf;

  assert(opa_sysfs_collect_port(NULL, "hfi1_0", 2) == -1);
  assert(opa_sysfs_collect_port(stats, NULL, 2) == -1);
  assert(opa_sysfs_collect_port(stats, "hfi1_0", 0) == -1);
  assert(opa_sysfs_collect_hw_counters(NULL, "hfi1_0", 2) == -1);
  assert(opa_sysfs_collect_classic_counters(stats, "hfi1_0", -1) == -1);
}

int main(void)
{
  test_classic_mapped_keys();
  test_classic_unmapped_and_null();
  test_hw_mapped_keys();
  test_hw_unmapped_and_null();
  test_collect_null_and_bad_port();
  test_collect_classic_success_skips_hw();
  test_collect_classic_miss_uses_hw();
  printf("test_opa_sysfs_map passed\n");
  return 0;
}
