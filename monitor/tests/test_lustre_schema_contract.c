#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "lustre_llite.h"

#define X SCHEMA_DEF
static const char lustre_llite_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "lustre_mdc.h"
#define X SCHEMA_DEF
static const char lustre_mdc_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "lustre_osc.h"
#define X SCHEMA_DEF
static const char lustre_osc_schema_def[] = JOIN(KEYS);
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
  assert_present(lustre_llite_schema_def, " vfs_read_bytes,E,U=B");
  assert_present(lustre_llite_schema_def, " vfs_write_bytes,E,U=B");
  assert_present(lustre_llite_schema_def, " vfs_direct_read_bytes,E,U=B");
  assert_present(lustre_llite_schema_def, " vfs_getattr_ops,E");
  assert_present(lustre_llite_schema_def, " fs_bytes_total,U=B");
  assert_present(lustre_llite_schema_def, " fs_files_free,");
  assert_absent(lustre_llite_schema_def, " read_bytes,E,U=B");
  assert_absent(lustre_llite_schema_def, " getattr,E");

  assert_present(lustre_mdc_schema_def, " mds_getattr,E");
  assert_present(lustre_mdc_schema_def, " reqs,E");
  assert_present(lustre_mdc_schema_def, " wait,E,U=us");

  assert_present(lustre_osc_schema_def, " read_bytes,E,U=B");
  assert_present(lustre_osc_schema_def, " ost_write,E");
  assert_present(lustre_osc_schema_def, " wait,E,U=us");

  printf("test_lustre_schema_contract passed\n");
  return 0;
}
