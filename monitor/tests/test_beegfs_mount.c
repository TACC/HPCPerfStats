/* BeeGFS mount-entry helpers: fstype detection and cfgFile= extraction. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "beegfs_mount.h"

static void test_fstype(void)
{
  assert(beegfs_fstype_is_beegfs("beegfs") == 1);
  assert(beegfs_fstype_is_beegfs("beegfs_nodev") == 1);
  assert(beegfs_fstype_is_beegfs("lustre") == 0);
  assert(beegfs_fstype_is_beegfs(NULL) == 0);
}

static void test_cfgfile(void)
{
  char cfg[256];
  char tiny[8];

  assert(beegfs_cfgfile_from_mnt_opts("rw,relatime,cfgFile=/etc/beegfs/beegfs-client.conf", cfg,
                                      sizeof(cfg)) == 0);
  assert(strcmp(cfg, "/etc/beegfs/beegfs-client.conf") == 0);
  assert(beegfs_cfgfile_from_mnt_opts("cfgFile=/etc/beegfs/a.conf,_netdev", cfg, sizeof(cfg)) == 0);
  assert(strcmp(cfg, "/etc/beegfs/a.conf") == 0);
  assert(beegfs_cfgfile_from_mnt_opts("rw,relatime", cfg, sizeof(cfg)) != 0);
  assert(beegfs_cfgfile_from_mnt_opts("rw,cfgFile=,ro", cfg, sizeof(cfg)) != 0);
  assert(beegfs_cfgfile_from_mnt_opts("cfgFile=/etc/beegfs/long.conf", tiny, sizeof(tiny)) != 0);
  assert(beegfs_cfgfile_from_mnt_opts(NULL, cfg, sizeof(cfg)) != 0);
}

int main(void)
{
  test_fstype();
  test_cfgfile();
  puts("test_beegfs_mount: OK");
  return 0;
}
