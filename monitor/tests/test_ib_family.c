/* ib_family_disable_all disables the host_ib stats type. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "ib_family.h"
#include "stats.h"

static struct stats_type g_host_ib;
static int g_stats_type_get_returns_null;

struct stats_type *stats_type_get(const char *name)
{
  if (g_stats_type_get_returns_null)
    return NULL;
  if (name != NULL && strcmp(name, "host_ib") == 0)
    return &g_host_ib;
  return NULL;
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void) type;
  (void) dev;
  return NULL;
}

int path_collect_key_value_dir(const char *path, struct stats *stats)
{
  (void) path;
  (void) stats;
  return 0;
}

static void test_disable_all_clears_enabled(void)
{
  memset(&g_host_ib, 0, sizeof(g_host_ib));
  snprintf(g_host_ib.st_name, sizeof(g_host_ib.st_name), "%s", "host_ib");
  g_host_ib.st_enabled = 1;

  ib_family_disable_all();
  assert(g_host_ib.st_enabled == 0);
}

static void test_disable_all_missing_type_is_noop(void)
{
  g_host_ib.st_enabled = 1;
  g_stats_type_get_returns_null = 1;
  ib_family_disable_all();
  assert(g_host_ib.st_enabled == 1);
  g_stats_type_get_returns_null = 0;
}

int main(void)
{
  test_disable_all_clears_enabled();
  test_disable_all_missing_type_is_noop();
  printf("test_ib_family passed\n");
  return 0;
}
