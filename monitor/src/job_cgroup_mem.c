/* Job-scope cgroup v2 memory.current / peak / stat (token dir only). */
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "job_cgroup_mem.h"
#include "job_proc_discover.h"
#include "path_read.h"

#define JOB_CGROUP_PATH_MAX 768

static const struct path_read_opts g_quiet_read_opts = {
    .skip_known_bad = 0,
    .report_errors = 0,
    .detect_overflow = 0,
};

static int path_has_dotdot(const char *p)
{
  const char *s = p;

  if (s == NULL)
    return 1;
  while (*s) {
    if (s[0] == '.' && s[1] == '.' && (s[2] == '/' || s[2] == '\0'))
      return 1;
    s++;
  }
  return 0;
}

const char *job_cgroup_mem_cgroup_root(void)
{
  const char *env = getenv("HPCPERFSTATS_CGROUP_ROOT");

  if (env == NULL || env[0] != '/' || path_has_dotdot(env))
    return "/sys/fs/cgroup";
  return env;
}

static unsigned long long bytes_to_kb(unsigned long long bytes)
{
  return bytes / 1024ULL;
}

static int read_ull_file(const char *path, unsigned long long *out)
{
  char buf[64];
  size_t len = 0;
  char *end = NULL;
  unsigned long long v;

  if (path == NULL || out == NULL)
    return -1;
  if (path_read_small(path, buf, sizeof(buf), &len, &g_quiet_read_opts) != 0)
    return -1;
  errno = 0;
  v = strtoull(buf, &end, 10);
  if (end == buf || errno == ERANGE)
    return -1;
  *out = v;
  return 0;
}

static int basename_is(const char *path, const char *name)
{
  const char *slash;

  if (path == NULL || name == NULL)
    return 0;
  slash = strrchr(path, '/');
  if (slash == NULL)
    return strcmp(path, name) == 0;
  return strcmp(slash + 1, name) == 0;
}

/*! Walk leaf relpath up to slurmstepd.scope/<token> directory (absolute under root). */
static int resolve_token_dir(const char *root, const char *relpath, char *out, size_t out_cap)
{
  char cur[JOB_CGROUP_PATH_MAX];
  char parent[JOB_CGROUP_PATH_MAX];
  int n;
  size_t root_len;

  if (root == NULL || relpath == NULL || out == NULL || out_cap == 0)
    return -1;
  if (relpath[0] != '/' || path_has_dotdot(relpath) || path_has_dotdot(root))
    return -1;
  root_len = strlen(root);
  while (root_len > 1 && root[root_len - 1] == '/')
    root_len--;
  n = snprintf(cur, sizeof(cur), "%.*s%s", (int)root_len, root, relpath);
  if (n < 0 || (size_t)n >= sizeof(cur))
    return -1;

  for (;;) {
    char *slash;

    slash = strrchr(cur, '/');
    if (slash == NULL || slash == cur)
      return -1;
    memcpy(parent, cur, (size_t)(slash - cur));
    parent[slash - cur] = '\0';
    if (basename_is(parent, "slurmstepd.scope")) {
      /* cur is .../slurmstepd.scope/<token>, the job scope. */
      if (basename_is(cur, "slurmstepd.scope"))
        return -1; /* never the bare aggregate */
      if (strlen(cur) + 1 > out_cap)
        return -1;
      memcpy(out, cur, strlen(cur) + 1);
      return 0;
    }
    if (strcmp(parent, root) == 0 || parent[0] == '\0')
      return -1;
    memcpy(cur, parent, strlen(parent) + 1);
  }
}

/* Order matches the JOB_CGROUP_MEM_STAT_* bits. */
static const struct {
  const char *key;
  size_t off;
} g_stat_fields[] = {
    {"anon", offsetof(struct job_cgroup_mem_kb, anon_pages)},
    {"file", offsetof(struct job_cgroup_mem_kb, file_pages)},
    {"file_mapped", offsetof(struct job_cgroup_mem_kb, mapped)},
    {"file_dirty", offsetof(struct job_cgroup_mem_kb, dirty)},
    {"file_writeback", offsetof(struct job_cgroup_mem_kb, writeback)},
    {"pagetables", offsetof(struct job_cgroup_mem_kb, page_tables)},
};

static void parse_memory_stat(const char *text, struct job_cgroup_mem_kb *out)
{
  const char *p = text;
  size_t i;

  if (text == NULL || out == NULL)
    return;
  while (*p) {
    char key[64];
    unsigned long long val = 0;
    int n = 0;

    if (sscanf(p, "%63s %llu%n", key, &val, &n) >= 2 && n > 0) {
      for (i = 0; i < sizeof(g_stat_fields) / sizeof(g_stat_fields[0]); i++)
        if (strcmp(key, g_stat_fields[i].key) == 0) {
          *(unsigned long long *)((char *)out + g_stat_fields[i].off) = bytes_to_kb(val);
          out->stat_present |= 1u << i;
        }
      p += n;
    } else {
      const char *nl = strchr(p, '\n');

      if (nl == NULL)
        break;
      p = nl + 1;
      continue;
    }
    while (*p == ' ' || *p == '\t')
      p++;
    if (*p == '\n')
      p++;
  }
}

int job_cgroup_mem_read(const char *cgroup_relpath, struct job_cgroup_mem_kb *out)
{
  const char *root;
  char token_dir[JOB_CGROUP_PATH_MAX];
  char path[JOB_CGROUP_PATH_MAX];
  unsigned long long cur_bytes = 0;
  unsigned long long peak_bytes = 0;
  char stat_buf[4096];
  size_t stat_len = 0;

  if (out == NULL)
    return -1;
  memset(out, 0, sizeof(*out));
  if (cgroup_relpath == NULL || cgroup_relpath[0] == '\0')
    return -1;

  root = job_cgroup_mem_cgroup_root();
  if (resolve_token_dir(root, cgroup_relpath, token_dir, sizeof(token_dir)) < 0)
    return -1;

  if (snprintf(path, sizeof(path), "%s/memory.current", token_dir) < 0 ||
      (size_t)strlen(path) >= sizeof(path))
    return -1;
  if (read_ull_file(path, &cur_bytes) < 0)
    return -1;
  out->mem_used = bytes_to_kb(cur_bytes);

  if (snprintf(path, sizeof(path), "%s/memory.peak", token_dir) >= 0 &&
      (size_t)strlen(path) < sizeof(path) && read_ull_file(path, &peak_bytes) == 0) {
    out->mem_peak = bytes_to_kb(peak_bytes);
    out->has_peak = 1;
  }

  errno = 0;
  if (snprintf(path, sizeof(path), "%s/memory.stat", token_dir) >= 0 &&
      (size_t)strlen(path) < sizeof(path) &&
      path_read_small(path, stat_buf, sizeof(stat_buf), &stat_len, &g_quiet_read_opts) == 0)
    parse_memory_stat(stat_buf, out);
  else
    out->stat_errno = errno != 0 ? errno : EIO;

  return 0;
}

int job_cgroup_mem_format_dev(const char *jid, char *out, size_t out_cap)
{
  int n;

  if (jid == NULL || jid[0] == '\0' || out == NULL || out_cap < 4)
    return -1;
  n = snprintf(out, out_cap, "j:%s", jid);
  if (n < 0 || (size_t)n >= out_cap)
    return -1;
  return 0;
}

int job_cgroup_mem_keep_dev(const char *jid, const char *dev)
{
  char want[2 + JOB_PROC_JID_MAX + 1];

  if (jid == NULL || jid[0] == '\0')
    return 0;
  if (job_cgroup_mem_format_dev(jid, want, sizeof(want)) < 0)
    return 0;
  return dev != NULL && strcmp(dev, want) == 0;
}
