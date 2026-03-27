#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "collect.h"
#include "stats.h"

/* collect.c pulls in stats_set for other entry points; stub for this test link. */
void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  (void)stats;
  (void)key;
  (void)val;
}

int main(void)
{
  char tmpl[] = "/tmp/hps_pc_testXXXXXX";
  int fd = mkstemp(tmpl);
  assert(fd >= 0);
  const char *one = "12345\n";
  assert(write(fd, one, strlen(one)) == (ssize_t)strlen(one));
  close(fd);

  unsigned long long v;
  assert(path_collect_single(tmpl, &v) == 1);
  assert(v == 12345ULL);
  unlink(tmpl);

  /* mkstemp overwrites XXXXXX in place; restore template for a second file. */
  strcpy(tmpl, "/tmp/hps_pc_testXXXXXX");
  fd = mkstemp(tmpl);
  assert(fd >= 0);
  const char *three = "10 20  30\n";
  assert(write(fd, three, strlen(three)) == (ssize_t)strlen(three));
  close(fd);
  unsigned long long a, b, c;
  assert(path_collect_list(tmpl, &a, &b, &c, NULL) == 3);
  assert(a == 10ULL && b == 20ULL && c == 30ULL);
  unlink(tmpl);

  printf("test_path_collect passed\n");
  return 0;
}
