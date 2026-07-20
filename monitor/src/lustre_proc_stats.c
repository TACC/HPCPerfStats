/* Shared Lustre proc/sysfs stats file helpers. */
#include "lustre_proc_stats.h"

#include <stdlib.h>
#include <string.h>

#include "fileio.h"
#include "string1.h"

int lustre_parse_samples_count(const char *rest, unsigned long long *count, unsigned long long *sum)
{
  unsigned long long c = 0;
  unsigned long long s = 0;
  int n;

  if (rest == NULL || count == NULL)
    return 0;

  n = sscanf(rest, "%llu samples %*s %*u %*u %llu", &c, &s);
  if (n == 2) {
    *count = c;
    if (sum != NULL)
      *sum = s;
    return 2;
  }

  n = sscanf(rest, "%llu samples", &c);
  if (n == 1) {
    *count = c;
    if (sum != NULL)
      *sum = 0;
    return 1;
  }
  return 0;
}

int lustre_parse_kv_ull(const char *line, const char *want_key, unsigned long long *value)
{
  char *buf = NULL;
  char *p;
  char *key;
  unsigned long long v = 0;

  if (line == NULL || want_key == NULL || value == NULL)
    return -1;

  buf = strdup(line);
  if (buf == NULL)
    return -1;

  p = buf;
  key = wsep(&p);
  if (key == NULL || p == NULL || strcmp(key, want_key) != 0) {
    free(buf);
    return -1;
  }
  if (sscanf(p, "%llu", &v) != 1) {
    free(buf);
    return -1;
  }
  *value = v;
  free(buf);
  return 0;
}

int lustre_fopen_obd_named(const char *dir, const char *d_name, const char *const *names,
                           size_t nnames, char **path_out, FILE **fp_out)
{
  size_t i;

  if (dir == NULL || d_name == NULL || names == NULL || path_out == NULL || fp_out == NULL)
    return -1;

  *path_out = NULL;
  *fp_out = NULL;

  for (i = 0; i < nnames; i++) {
    char *path = NULL;
    FILE *fp;

    if (names[i] == NULL)
      continue;
    /* Use file_fopen_read (not path_file_fopen_read) so a missing preferred
     * modern file does not permanently skip that path when only legacy exists. */
    if (asprintf(&path, "%s/%s/%s", dir, d_name, names[i]) < 0)
      return -1;
    fp = file_fopen_read(path);
    if (fp != NULL) {
      *path_out = path;
      *fp_out = fp;
      return 0;
    }
    free(path);
  }
  return -1;
}
