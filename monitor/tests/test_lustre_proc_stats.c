/* Unit tests for lustre_proc_stats helpers (modern + legacy sample lines). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "lustre_proc_stats.h"

static void test_parse_classic_samples(void)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;
  int n;

  n = lustre_parse_samples_count("123 samples [bytes] 1 2 456789", &count, &sum);
  assert(n == 2);
  assert(count == 123ULL);
  assert(sum == 456789ULL);
}

static void test_parse_modern_samples(void)
{
  unsigned long long count = 0;
  unsigned long long sum = 99;
  int n;

  n = lustre_parse_samples_count("9249 samples [reqs]", &count, &sum);
  assert(n == 1);
  assert(count == 9249ULL);
  assert(sum == 0ULL);
}

static void test_parse_kv(void)
{
  unsigned long long v = 0;

  assert(lustre_parse_kv_ull("kbytestotal\t1048576\n", "kbytestotal", &v) == 0);
  assert(v == 1048576ULL);
  assert(lustre_parse_kv_ull("other 1", "kbytestotal", &v) < 0);
}

static void test_fopen_prefer_modern(void)
{
  char tmpl[] = "/tmp/lustre_proc_XXXXXX";
  char *root;
  char obd_dir[256];
  char modern_path[288];
  char legacy_path[288];
  FILE *fp;
  char *path_out = NULL;
  FILE *fp_out = NULL;
  static const char *const names[] = {"md_stats", "stats"};
  char buf[64];

  root = mkdtemp(tmpl);
  assert(root != NULL);
  snprintf(obd_dir, sizeof(obd_dir), "%s/obd0", root);
  assert(mkdir(obd_dir, 0700) == 0);

  snprintf(legacy_path, sizeof(legacy_path), "%s/stats", obd_dir);
  fp = fopen(legacy_path, "w");
  assert(fp != NULL);
  fputs("legacy 1 samples [reqs]\n", fp);
  fclose(fp);

  snprintf(modern_path, sizeof(modern_path), "%s/md_stats", obd_dir);
  fp = fopen(modern_path, "w");
  assert(fp != NULL);
  fputs("getattr 42 samples [reqs]\n", fp);
  fclose(fp);

  assert(lustre_fopen_obd_named(root, "obd0", names, 2, &path_out, &fp_out) == 0);
  assert(path_out != NULL && fp_out != NULL);
  assert(strstr(path_out, "md_stats") != NULL);
  assert(fgets(buf, sizeof(buf), fp_out) != NULL);
  assert(strstr(buf, "getattr") != NULL);
  fclose(fp_out);
  free(path_out);

  unlink(modern_path);
  path_out = NULL;
  fp_out = NULL;
  assert(lustre_fopen_obd_named(root, "obd0", names, 2, &path_out, &fp_out) == 0);
  assert(strstr(path_out, "/stats") != NULL);
  fclose(fp_out);
  free(path_out);

  unlink(legacy_path);
  rmdir(obd_dir);
  rmdir(root);
}

int main(void)
{
  test_parse_classic_samples();
  test_parse_modern_samples();
  test_parse_kv();
  test_fopen_prefer_modern();
  printf("test_lustre_proc_stats passed\n");
  return 0;
}
