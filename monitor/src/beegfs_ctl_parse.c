/* Pure BeeGFS clientstats / mount-option parsers (unit-tested). */
#include "beegfs_ctl_parse.h"

#include <ctype.h>
#include <string.h>
#include <stdlib.h>

int beegfs_fstype_is_beegfs(const char *fstype)
{
  if (fstype == NULL)
    return 0;
  return (strcmp(fstype, "beegfs") == 0 || strcmp(fstype, "beegfs_nodev") == 0) ? 1 : 0;
}

int beegfs_cfgfile_from_mnt_opts(const char *opts, char *out, size_t out_sz)
{
  const char *p;
  const char *key = "cfgFile=";
  size_t key_len = 8; /* strlen("cfgFile=") */
  size_t n;

  if (opts == NULL || out == NULL || out_sz == 0)
    return -1;

  p = strstr(opts, key);
  if (p == NULL)
    return -1;
  p += key_len;
  n = 0;
  while (p[n] != '\0' && p[n] != ',' && !isspace((unsigned char)p[n]))
    n++;
  if (n == 0 || n >= out_sz)
    return -1;
  memcpy(out, p, n);
  out[n] = '\0';
  return 0;
}

int beegfs_ctl_line_is_sum(const char *line)
{
  const char *p;

  if (line == NULL)
    return 0;
  p = line;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  return (strncmp(p, "Sum:", 4) == 0) ? 1 : 0;
}

static int beegfs_token_eq(const char *a, size_t a_len, const char *b)
{
  size_t b_len;

  if (a == NULL || b == NULL)
    return 0;
  b_len = strlen(b);
  if (a_len != b_len)
    return 0;
  return strncasecmp(a, b, a_len) == 0;
}

int beegfs_ctl_line_matches_local(const char *line, const char *const *idents, size_t n_idents)
{
  const char *p;
  const char *start;
  size_t len;
  size_t i;

  if (line == NULL || idents == NULL || n_idents == 0)
    return 0;
  if (beegfs_ctl_line_is_sum(line))
    return 0;

  p = line;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  start = p;
  while (*p != '\0' && !isspace((unsigned char)*p))
    p++;
  len = (size_t)(p - start);
  if (len == 0)
    return 0;

  for (i = 0; i < n_idents; i++) {
    if (idents[i] != NULL && beegfs_token_eq(start, len, idents[i]))
      return 1;
  }
  return 0;
}

static void beegfs_ctl_set_u64(struct beegfs_ctl_counters *out, const char *key, double raw,
                               int scale_mib)
{
  unsigned long long v;

  if (out == NULL || key == NULL)
    return;
  if (raw < 0.0)
    raw = 0.0;
  if (scale_mib)
    v = (unsigned long long)(raw * (double)BEEGFS_CTL_MIB_TO_BYTES + 0.5);
  else
    v = (unsigned long long)(raw + 0.5);

  if (strcmp(key, "MiB-rd") == 0 || strcmp(key, "B-rd") == 0) {
    out->vfs_read_bytes = v;
    out->have_vfs_read_bytes = 1;
  } else if (strcmp(key, "MiB-wr") == 0 || strcmp(key, "B-wr") == 0) {
    out->vfs_write_bytes = v;
    out->have_vfs_write_bytes = 1;
  } else if (strcmp(key, "ops-rd") == 0) {
    out->vfs_read_ops = v;
    out->have_vfs_read_ops = 1;
  } else if (strcmp(key, "ops-wr") == 0) {
    out->vfs_write_ops = v;
    out->have_vfs_write_ops = 1;
  } else if (strcmp(key, "open") == 0) {
    out->vfs_open_ops = v;
    out->have_vfs_open_ops = 1;
  } else if (strcmp(key, "close") == 0) {
    out->vfs_close_ops = v;
    out->have_vfs_close_ops = 1;
  } else if (strcmp(key, "stat") == 0) {
    out->vfs_getattr_ops = v;
    out->have_vfs_getattr_ops = 1;
  } else if (strcmp(key, "sAttr") == 0) {
    out->vfs_setattr_ops = v;
    out->have_vfs_setattr_ops = 1;
  } else if (strcmp(key, "trunc") == 0) {
    out->vfs_truncate_ops = v;
    out->have_vfs_truncate_ops = 1;
  } else if (strcmp(key, "rddir") == 0) {
    out->vfs_readdir_ops = v;
    out->have_vfs_readdir_ops = 1;
  } else if (strcmp(key, "create") == 0) {
    out->vfs_create_ops = v;
    out->have_vfs_create_ops = 1;
  } else if (strcmp(key, "mkdir") == 0) {
    out->vfs_mkdir_ops = v;
    out->have_vfs_mkdir_ops = 1;
  } else if (strcmp(key, "rmdir") == 0) {
    out->vfs_rmdir_ops = v;
    out->have_vfs_rmdir_ops = 1;
  } else if (strcmp(key, "ren") == 0) {
    out->vfs_rename_ops = v;
    out->have_vfs_rename_ops = 1;
  } else if (strcmp(key, "unlnk") == 0) {
    out->vfs_unlink_ops = v;
    out->have_vfs_unlink_ops = 1;
  } else if (strcmp(key, "hardlnk") == 0) {
    out->vfs_link_ops = v;
    out->have_vfs_link_ops = 1;
  } else if (strcmp(key, "statfs") == 0) {
    out->vfs_statfs_ops = v;
    out->have_vfs_statfs_ops = 1;
  }
}

int beegfs_ctl_parse_stats_line(const char *line, struct beegfs_ctl_counters *out)
{
  const char *p;
  char key[64];

  if (line == NULL || out == NULL)
    return -1;
  if (beegfs_ctl_line_is_sum(line))
    return -1;

  memset(out, 0, sizeof(*out));
  p = line;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  /* Skip node id field. */
  while (*p != '\0' && !isspace((unsigned char)*p))
    p++;

  while (*p != '\0') {
    char *end = NULL;
    double raw;
    size_t klen;
    int scale_mib;

    while (*p != '\0' && isspace((unsigned char)*p))
      p++;
    if (*p == '\0')
      break;

    raw = strtod(p, &end);
    if (end == p) {
      /* Non-numeric token; skip to next whitespace. */
      while (*p != '\0' && !isspace((unsigned char)*p))
        p++;
      continue;
    }
    p = end;
    while (*p != '\0' && isspace((unsigned char)*p))
      p++;
    if (*p != '[')
      continue;
    p++;
    klen = 0;
    while (*p != '\0' && *p != ']' && klen + 1 < sizeof(key)) {
      key[klen++] = *p++;
    }
    key[klen] = '\0';
    if (*p == ']')
      p++;

    scale_mib = (strcmp(key, "MiB-rd") == 0 || strcmp(key, "MiB-wr") == 0) ? 1 : 0;
    beegfs_ctl_set_u64(out, key, raw, scale_mib);
  }
  return 0;
}

int beegfs_ctl_select_local_line(const char *text, const char *const *idents, size_t n_idents,
                                 struct beegfs_ctl_counters *out)
{
  const char *p;
  char line[4096];

  if (text == NULL || out == NULL)
    return 0;

  p = text;
  while (*p != '\0') {
    size_t n = 0;
    while (p[n] != '\0' && p[n] != '\n' && n + 1 < sizeof(line)) {
      line[n] = p[n];
      n++;
    }
    line[n] = '\0';
    if (beegfs_ctl_line_matches_local(line, idents, n_idents)) {
      if (beegfs_ctl_parse_stats_line(line, out) == 0)
        return 1;
    }
    p += n;
    if (*p == '\n')
      p++;
  }
  return 0;
}
