#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "beegfs_client.h"

#define X SCHEMA_DEF
static const char beegfs_client_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

static void assert_absent(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) == NULL);
}

int main(void)
{
  assert_present(beegfs_client_schema_def, " vfs_read_bytes,E,U=B");
  assert_present(beegfs_client_schema_def, " vfs_write_bytes,E,U=B");
  assert_present(beegfs_client_schema_def, " vfs_read_ops,E");
  assert_present(beegfs_client_schema_def, " vfs_write_ops,E");
  assert_present(beegfs_client_schema_def, " vfs_open_ops,E");
  assert_present(beegfs_client_schema_def, " vfs_getattr_ops,E");
  assert_present(beegfs_client_schema_def, " vfs_statfs_ops,E");
  assert_present(beegfs_client_schema_def, " fs_bytes_total,U=B");
  assert_present(beegfs_client_schema_def, " fs_files_free,");
  /* Reject raw ctl abbreviations as emit names. */
  assert_absent(beegfs_client_schema_def, " MiB-rd");
  assert_absent(beegfs_client_schema_def, " ops-rd");
  assert_absent(beegfs_client_schema_def, " unlnk");
  assert_absent(beegfs_client_schema_def, " inode_cache");
  puts("test_beegfs_schema_contract: OK");
  return 0;
}
