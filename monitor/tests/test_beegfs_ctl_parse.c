#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "beegfs_ctl_parse.h"

static char *read_fixture(const char *relpath)
{
  char path[512];
  FILE *fp;
  long sz;
  char *buf;
  size_t n;
  const char *srcdir;

  srcdir = getenv("srcdir");
  if (srcdir == NULL)
    srcdir = ".";
  snprintf(path, sizeof(path), "%s/%s", srcdir, relpath);
  fp = fopen(path, "r");
  if (fp == NULL) {
    snprintf(path, sizeof(path), "%s", relpath);
    fp = fopen(path, "r");
  }
  assert(fp != NULL);
  assert(fseek(fp, 0, SEEK_END) == 0);
  sz = ftell(fp);
  assert(sz >= 0);
  assert(fseek(fp, 0, SEEK_SET) == 0);
  buf = malloc((size_t)sz + 1);
  assert(buf != NULL);
  n = fread(buf, 1, (size_t)sz, fp);
  buf[n] = '\0';
  fclose(fp);
  return buf;
}

static void test_fstype_and_cfgfile(void)
{
  char cfg[256];

  assert(beegfs_fstype_is_beegfs("beegfs") == 1);
  assert(beegfs_fstype_is_beegfs("beegfs_nodev") == 1);
  assert(beegfs_fstype_is_beegfs("lustre") == 0);
  assert(beegfs_cfgfile_from_mnt_opts("rw,relatime,cfgFile=/etc/beegfs/beegfs-client.conf", cfg,
                                      sizeof(cfg)) == 0);
  assert(strcmp(cfg, "/etc/beegfs/beegfs-client.conf") == 0);
  assert(beegfs_cfgfile_from_mnt_opts("rw,relatime", cfg, sizeof(cfg)) != 0);
}

static void test_sum_never_selected(void)
{
  const char *idents[] = {"192.168.40.11"};
  struct beegfs_ctl_counters c;
  char *text = read_fixture("fixtures/beegfs/clientstats_storage.txt");

  assert(beegfs_ctl_line_is_sum("Sum: 1 [ops-rd]") == 1);
  assert(beegfs_ctl_line_is_sum("  Sum: 1 [ops-rd]") == 1);
  assert(beegfs_ctl_select_local_line(text, idents, 1, &c) == 1);
  /* Local MiB-rd 80 → bytes */
  assert(c.have_vfs_read_bytes == 1);
  assert(c.vfs_read_bytes == 80ULL * BEEGFS_CTL_MIB_TO_BYTES);
  assert(c.vfs_write_bytes == 4ULL * BEEGFS_CTL_MIB_TO_BYTES);
  assert(c.vfs_read_ops == 100ULL);
  assert(c.vfs_write_ops == 20ULL);
  assert(c.vfs_statfs_ops == 3ULL);
  free(text);
}

static void test_hostname_meta_line(void)
{
  const char *idents[] = {"c512-122", "c512-122.stampede3.tacc.utexas.edu"};
  struct beegfs_ctl_counters c;
  char *text = read_fixture("fixtures/beegfs/clientstats_meta.txt");

  assert(beegfs_ctl_select_local_line(text, idents, 2, &c) == 1);
  assert(c.vfs_open_ops == 50ULL);
  assert(c.vfs_close_ops == 45ULL);
  assert(c.vfs_getattr_ops == 80ULL);
  assert(c.vfs_setattr_ops == 2ULL);
  assert(c.vfs_readdir_ops == 10ULL);
  assert(c.vfs_create_ops == 4ULL);
  assert(c.vfs_mkdir_ops == 1ULL);
  assert(c.vfs_rename_ops == 2ULL);
  assert(c.vfs_unlink_ops == 3ULL);
  assert(c.vfs_link_ops == 1ULL);
  free(text);
}

static void test_b_rd_no_scale(void)
{
  struct beegfs_ctl_counters c;
  const char *line = "10.1.2.3   1048576 [B-rd]  2048 [B-wr]  7 [ops-rd]";

  assert(beegfs_ctl_parse_stats_line(line, &c) == 0);
  assert(c.vfs_read_bytes == 1048576ULL);
  assert(c.vfs_write_bytes == 2048ULL);
  assert(c.vfs_read_ops == 7ULL);
}

static void test_sum_parse_rejected(void)
{
  struct beegfs_ctl_counters c;
  const char *line = "Sum:            9.5e+02 [MiB-rd]  5000 [ops-rd]";

  assert(beegfs_ctl_parse_stats_line(line, &c) != 0);
}

int main(void)
{
  test_fstype_and_cfgfile();
  test_sum_never_selected();
  test_hostname_meta_line();
  test_b_rd_no_scale();
  test_sum_parse_rejected();
  puts("test_beegfs_ctl_parse: OK");
  return 0;
}
