#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "fileio.h"
#include "pscanf.h"

int main(void)
{
  char tmpl[] = "/tmp/hps_pscanfXXXXXX";
  int fd = mkstemp(tmpl);

  assert(fd >= 0);
  const char *s = "12345 67\n";

  assert(write(fd, s, strlen(s)) == (ssize_t)strlen(s));
  close(fd);

  unsigned a, b;

  assert(pscanf(tmpl, "%u %u", &a, &b) == 2);
  assert(a == 12345u && b == 67u);

  FILE *fp = file_fopen_read(tmpl);

  assert(fp != NULL);
  assert(fgetc(fp) == '1');
  fclose(fp);

  unlink(tmpl);

  puts("test_pscanf passed");
  return 0;
}
