#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
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

static int argv_has(const struct beegfs_ctl_argv *av, const char *tok)
{
  int i;

  for (i = 0; i < av->argc; i++) {
    if (av->argv[i] != NULL && strcmp(av->argv[i], tok) == 0)
      return 1;
  }
  return 0;
}

static void test_clientstats_argv_equals_form(void)
{
  struct beegfs_ctl_argv av;
  int i;

  assert(beegfs_ctl_build_clientstats_argv(&av, "storage", "/etc/beegfs/beegfs-client.conf", 1) >
         0);
  assert(argv_has(&av, "--nodetype=storage"));
  assert(argv_has(&av, "--cfgFile=/etc/beegfs/beegfs-client.conf"));
  assert(argv_has(&av, "--rwunit=B"));
  assert(argv_has(&av, "--names"));
  assert(argv_has(&av, "--interval=0"));
  assert(argv_has(&av, "--perinterval"));
  for (i = 0; i < av.argc; i++) {
    assert(strcmp(av.argv[i], "--nodetype") != 0);
    assert(strcmp(av.argv[i], "--mount") != 0);
    assert(strcmp(av.argv[i], "--cfgFile") != 0);
  }
  assert(beegfs_ctl_build_clientstats_argv(&av, "meta", "/etc/beegfs/beegfs-client.conf", 0) > 0);
  assert(argv_has(&av, "--nodetype=meta"));
  assert(!argv_has(&av, "--rwunit=B"));
  assert(beegfs_ctl_build_clientstats_argv(&av, "storage", "relative.conf", 0) < 0);
  assert(beegfs_ctl_build_clientstats_argv(&av, "bogus", "/etc/beegfs/beegfs-client.conf", 0) < 0);
  assert(beegfs_path_is_safe("/etc/beegfs/beegfs-client.conf") == 1);
  assert(beegfs_path_is_safe("etc/beegfs/beegfs-client.conf") == 0);
}

static void test_idents_add_ib_aliases(void)
{
  char idents[8][BEEGFS_IDENT_LEN];
  size_t n;

  memset(idents, 0, sizeof(idents));
  snprintf(idents[0], BEEGFS_IDENT_LEN, "%s", "c317-016");
  snprintf(idents[1], BEEGFS_IDENT_LEN, "%s", "c317-016.ls6.tacc.utexas.edu");
  snprintf(idents[2], BEEGFS_IDENT_LEN, "%s", "192.168.43.10");
  snprintf(idents[3], BEEGFS_IDENT_LEN, "%s", "already-ib");
  n = beegfs_idents_add_ib_aliases(idents, 4, 8);
  assert(n == 6);
  assert(strcmp(idents[4], "c317-016-ib") == 0);
  assert(strcmp(idents[5], "c317-016.ls6.tacc.utexas.edu-ib") == 0);
  /* IPv4 and existing *-ib unchanged / not duplicated */
  n = beegfs_idents_add_ib_aliases(idents, n, 8);
  assert(n == 6);
}

int main(void)
{
  test_fstype_and_cfgfile();
  test_sum_never_selected();
  test_hostname_meta_line();
  test_b_rd_no_scale();
  test_sum_parse_rejected();
  test_clientstats_argv_equals_form();
  test_idents_add_ib_aliases();
  puts("test_beegfs_ctl_parse: OK");
  return 0;
}
