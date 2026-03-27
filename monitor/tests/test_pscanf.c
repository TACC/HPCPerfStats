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
  char huge[6000];

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

  /* Large file: small-buffer path detects truncation and falls back to slurp. */
  char tmpl2[] = "/tmp/hps_pscanfXXXXXX";
  fd = mkstemp(tmpl2);
  assert(fd >= 0);
  memset(huge, ' ', sizeof(huge));
  memcpy(huge, "424242 ", 7);
  memcpy(huge + sizeof(huge) - 8, " 99\n", 4);
  assert(write(fd, huge, sizeof(huge)) == (ssize_t)sizeof(huge));
  close(fd);
  a = 0;
  b = 0;
  assert(pscanf(tmpl2, "%u %u", &a, &b) == 2);
  assert(a == 424242u && b == 99u);
  unlink(tmpl2);

  /* Missing path */
  assert(pscanf("/nonexistent/hps_pscanf_path_zz", "%u", &a) == -1);

  puts("test_pscanf passed");
  return 0;
}
