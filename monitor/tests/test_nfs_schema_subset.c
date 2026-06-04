#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

/* nfs.c link stubs: this test validates schema text only. */
int str_collect_key_list(const char *str, struct stats *stats, ...)
{
  (void) str;
  (void) stats;
  return 0;
}

int str_collect_prefix_key_list(const char *str, struct stats *stats, const char *prefix, ...)
{
  (void) str;
  (void) stats;
  (void) prefix;
  return 0;
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void) type;
  (void) dev;
  return NULL;
}

void stats_set(struct stats *s, const char *key, unsigned long long val)
{
  (void) s;
  (void) key;
  (void) val;
}

extern struct stats_type nfs_stats_type;

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
  const char *schema = nfs_stats_type.st_schema_def;

  assert_present(schema, " normal_read,E,U=B");
  assert_present(schema, " direct_read,E,U=B");
  assert_present(schema, " server_read,E,U=B");
  assert_present(schema, " normal_write,E,U=B");
  assert_present(schema, " direct_write,E,U=B");
  assert_present(schema, " server_write,E,U=B");

  assert_present(schema, " xprt_bad_xids,E");
  assert_present(schema, " xprt_req_u,E");
  assert_present(schema, " xprt_bklog_u,E");
  assert_present(schema, " read_timeouts,E");
  assert_present(schema, " write_timeouts,E");
  assert_present(schema, " read_queue,E,U=ms");
  assert_present(schema, " write_queue,E,U=ms");
  assert_present(schema, " read_rtt,E,U=ms");
  assert_present(schema, " write_rtt,E,U=ms");
  assert_present(schema, " delay,E");

  assert_absent(schema, " inode_revalidate,E");
  assert_absent(schema, " xprt_sends,E");
  assert_absent(schema, " ACCESS_ops,E");
  assert_absent(schema, " READ_execute,E,U=ms");
  assert_absent(schema, " write_page,E");

  printf("test_nfs_schema_subset passed\n");
  return 0;
}
