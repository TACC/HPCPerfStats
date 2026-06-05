#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

/* lustre_llite KEYS from llite.c */
#define KEYS \
  X(read, "E", ""), \
  X(write, "E", ""), \
  X(read_bytes, "E,U=B", ""), \
  X(write_bytes, "E,U=B", ""), \
  X(direct_read, "E,U=B", ""), \
  X(direct_write, "E,U=B", ""), \
  X(osc_read, "E,U=B", ""), \
  X(osc_write, "E,U=B", ""), \
  X(dirty_pages_hits, "E", ""), \
  X(dirty_pages_misses, "E", ""), \
  X(ioctl, "E", ""), \
  X(open, "E", ""), \
  X(close, "E", ""), \
  X(mmap, "E", ""), \
  X(seek, "E", ""), \
  X(fsync, "E", ""), \
  X(setattr, "E", ""), \
  X(truncate, "E", ""), \
  X(flock, "E", ""), \
  X(getattr, "E", ""), \
  X(statfs, "E", ""), \
  X(alloc_inode, "E", ""), \
  X(setxattr, "E", ""), \
  X(getxattr, "E", ""), \
  X(listxattr, "E", ""), \
  X(removexattr, "E", ""), \
  X(inode_permission, "E", ""), \
  X(readdir, "E", ""), \
  X(create, "E", ""), \
  X(lookup, "E", ""), \
  X(link, "E", ""), \
  X(unlink, "E", ""), \
  X(symlink, "E", ""), \
  X(mkdir, "E", ""), \
  X(rmdir, "E", ""), \
  X(mknod, "E", ""), \
  X(rename, "E", "")
#define X SCHEMA_DEF
static const char lustre_llite_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* lustre_mdc KEYS from mdc.c */
#define KEYS \
  X(ldlm_cancel, "E", ""), \
  X(mds_close, "E", ""), \
  X(mds_getattr, "E", ""), \
  X(mds_getattr_lock, "E", ""), \
  X(mds_getxattr, "E", ""), \
  X(mds_readpage, "E", ""), \
  X(mds_statfs, "E", ""), \
  X(mds_sync, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")
#define X SCHEMA_DEF
static const char lustre_mdc_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* lustre_osc KEYS from osc.c */
#define KEYS \
  X(read_bytes, "E,U=B", ""), \
  X(write_bytes, "E,U=B", ""), \
  X(ost_destroy, "E", ""), \
  X(ost_punch, "E", ""), \
  X(ost_read, "E", ""), \
  X(ost_setattr, "E", ""), \
  X(ost_statfs, "E", ""), \
  X(ost_write, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")
#define X SCHEMA_DEF
static const char lustre_osc_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

int main(void)
{
  assert_present(lustre_llite_schema_def, " read_bytes,E,U=B");
  assert_present(lustre_llite_schema_def, " write_bytes,E,U=B");
  assert_present(lustre_llite_schema_def, " direct_read,E,U=B");
  assert_present(lustre_llite_schema_def, " getattr,E");

  assert_present(lustre_mdc_schema_def, " mds_getattr,E");
  assert_present(lustre_mdc_schema_def, " reqs,E");
  assert_present(lustre_mdc_schema_def, " wait,E,U=us");

  assert_present(lustre_osc_schema_def, " read_bytes,E,U=B");
  assert_present(lustre_osc_schema_def, " ost_write,E");
  assert_present(lustre_osc_schema_def, " wait,E,U=us");

  printf("test_lustre_schema_contract passed\n");
  return 0;
}
