/* Filtered collect helpers + global key-active hook (collect.c gating). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "collect.h"
#include "stats.h"

/* Record stats_set calls (collect.c is linked; stub the sink it stores into). */
#define MAX_REC 32
static char rec_key[MAX_REC][64];
static unsigned long long rec_val[MAX_REC];
static int rec_n;

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  (void) stats;
  assert(rec_n < MAX_REC);
  snprintf(rec_key[rec_n], sizeof(rec_key[0]), "%s", key);
  rec_val[rec_n] = val;
  rec_n++;
}

static void rec_reset(void)
{
  rec_n = 0;
}

static int rec_find(const char *key, unsigned long long *out)
{
  int i;
  for (i = 0; i < rec_n; i++)
    if (strcmp(rec_key[i], key) == 0) {
      if (out != NULL)
        *out = rec_val[i];
      return 1;
    }
  return 0;
}

/* Predicate: ctx is a NULL-terminated array of allowed key names. */
static int allow_only(void *ctx, struct stats *stats, const char *key)
{
  const char *const *allowed = ctx;
  (void) stats;
  for (; *allowed != NULL; allowed++)
    if (strcmp(*allowed, key) == 0)
      return 1;
  return 0;
}

/* Dummy non-NULL stats handle (helpers reject NULL; never dereferenced here). */
static struct stats *dummy_stats(void)
{
  static int placeholder;
  return (struct stats *) &placeholder;
}

static void write_file(const char *path, const char *content)
{
  FILE *f = fopen(path, "w");
  assert(f != NULL);
  assert(fputs(content, f) >= 0);
  fclose(f);
}

static void test_dir_filtered(void)
{
  char dir[] = "/tmp/hps_gate_dirXXXXXX";
  char path[256];
  const char *allowed[] = { "rx_bytes", NULL };

  assert(mkdtemp(dir) != NULL);
  snprintf(path, sizeof(path), "%s/rx_bytes", dir);
  write_file(path, "100\n");
  snprintf(path, sizeof(path), "%s/collisions", dir);
  write_file(path, "5\n");

  rec_reset();
  assert(path_collect_key_value_dir_filtered(dir, dummy_stats(), allow_only,
                                             (void *) allowed) == 0);
  {
    unsigned long long v = 0;
    assert(rec_find("rx_bytes", &v) && v == 100ULL);
    assert(!rec_find("collisions", NULL));
  }

  snprintf(path, sizeof(path), "%s/rx_bytes", dir);
  unlink(path);
  snprintf(path, sizeof(path), "%s/collisions", dir);
  unlink(path);
  rmdir(dir);
}

static void test_key_value_filtered(void)
{
  char file[] = "/tmp/hps_gate_kvXXXXXX";
  int fd = mkstemp(file);
  const char *allowed[] = { "k2", NULL };

  assert(fd >= 0);
  close(fd);
  write_file(file, "k1 1\nk2 2\nk3 3\n");

  rec_reset();
  assert(path_collect_key_value_filtered(file, dummy_stats(), allow_only,
                                         (void *) allowed) == 0);
  {
    unsigned long long v = 0;
    assert(rec_find("k2", &v) && v == 2ULL);
    assert(!rec_find("k1", NULL));
    assert(!rec_find("k3", NULL));
  }

  /* NULL predicate collects everything. */
  rec_reset();
  assert(path_collect_key_value_filtered(file, dummy_stats(), NULL, NULL) == 0);
  assert(rec_find("k1", NULL) && rec_find("k2", NULL) && rec_find("k3", NULL));

  unlink(file);
}

static void test_key_list_filtered(void)
{
  char file[] = "/tmp/hps_gate_klXXXXXX";
  int fd = mkstemp(file);
  const char *allowed[] = { "b", NULL };

  assert(fd >= 0);
  close(fd);
  write_file(file, "10 20 30\n");

  rec_reset();
  /* All three values consumed positionally, only "b" stored. */
  assert(path_collect_key_list_filtered(file, dummy_stats(), allow_only,
                                        (void *) allowed, "a", "b", "c", NULL) == 3);
  {
    unsigned long long v = 0;
    assert(rec_find("b", &v) && v == 20ULL);
    assert(!rec_find("a", NULL));
    assert(!rec_find("c", NULL));
  }

  unlink(file);
}

static void test_global_hook(void)
{
  char file[] = "/tmp/hps_gate_hookXXXXXX";
  int fd = mkstemp(file);
  const char *allowed[] = { "k1", NULL };

  assert(fd >= 0);
  close(fd);
  write_file(file, "k1 7\nk2 8\n");

  /* With the hook installed, non-filtered helper gates by the predicate. */
  collect_set_key_active_hook(allow_only, (void *) allowed);
  rec_reset();
  assert(path_collect_key_value(file, dummy_stats()) == 0);
  assert(rec_find("k1", NULL));
  assert(!rec_find("k2", NULL));

  /* Clearing the hook restores unconditional collection. */
  collect_set_key_active_hook(NULL, NULL);
  rec_reset();
  assert(path_collect_key_value(file, dummy_stats()) == 0);
  assert(rec_find("k1", NULL) && rec_find("k2", NULL));

  unlink(file);
}

int main(void)
{
  test_dir_filtered();
  test_key_value_filtered();
  test_key_list_filtered();
  test_global_hook();
  printf("test_collect_tier_gate passed\n");
  return 0;
}
