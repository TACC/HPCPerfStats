/* Discover Slurm jids via slurmstepd.scope token groups: SLURM_JOB_ID environ, then the
 * root slurmstepd process title for tokens no job process named (idle allocations). */
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "job_cgroup_mem.h"
#include "job_proc_discover.h"
#include "monitor_log.h"
#include "proc_filter.h"

#define JOB_PROC_TOKEN_MAX 64
#define JOB_PROC_SCOPE_MAX 512
/* slurmstepd.scope location under the cgroup v2 root (Slurm cgroup/v2 plugin). */
#define JOB_PROC_STEPD_SCOPE_REL "/system.slice/slurmstepd.scope"
#define JOB_PROC_STEPD_TITLE_PREFIX "slurmstepd: ["
#define JOB_PROC_CMDLINE_MAX 128

struct scope_bucket {
  char token[JOB_PROC_TOKEN_MAX];
  int *pids;
  size_t npids;
  size_t cap;
  char jid[JOB_PROC_JID_MAX + 1];
  char job_gpus[JOB_PROC_GPUS_ENV_MAX];
  char cgroup_relpath[JOB_PROC_CGROUP_RELPATH_MAX];
  int have_step;
  int named;
  int named_from_title;
};

static int g_discover_warned;

static int path_has_dotdot(const char *p)
{
  const char *s = p;

  if (s == NULL)
    return 1;
  while (*s) {
    if (s[0] == '.' && s[1] == '.' && (s[2] == '/' || s[2] == '\0'))
      return 1;
    if (*s == '/')
      s++;
    else
      s++;
  }
  return 0;
}

const char *job_proc_discover_proc_root(void)
{
  const char *env = getenv("HPCPERFSTATS_PROC_ROOT");

  if (env == NULL || env[0] != '/' || path_has_dotdot(env))
    return "/proc";
  return env;
}

int job_proc_jid_acceptable(const char *jid)
{
  size_t i;
  size_t n;

  if (jid == NULL || jid[0] == '\0')
    return 0;
  if (strcmp(jid, "0") == 0 || strcmp(jid, "-") == 0)
    return 0;
  n = strlen(jid);
  if (n > JOB_PROC_JID_MAX)
    return 0;
  for (i = 0; i < n; i++) {
    if (isspace((unsigned char)jid[i]))
      return 0;
  }
  return 1;
}

void job_proc_discover_result_clear(struct job_proc_discover_result *r)
{
  size_t i;

  if (r == NULL)
    return;
  for (i = 0; i < r->n; i++) {
    free(r->entries[i].pids);
    r->entries[i].pids = NULL;
    r->entries[i].npids = 0;
  }
  r->n = 0;
}

static int bucket_add_pid(struct scope_bucket *b, int pid)
{
  size_t i;

  for (i = 0; i < b->npids; i++) {
    if (b->pids[i] == pid)
      return 0;
  }
  if (b->npids + 1 > b->cap) {
    size_t ncap = b->cap ? b->cap * 2 : 8;
    int *np = realloc(b->pids, ncap * sizeof(*np));

    if (np == NULL)
      return -1;
    b->pids = np;
    b->cap = ncap;
  }
  b->pids[b->npids++] = pid;
  return 0;
}

static int parse_scope_token(const char *cgroup, char *token_out, size_t token_cap)
{
  const char *p;
  const char *slash;
  size_t len;

  if (cgroup == NULL || token_out == NULL || token_cap == 0)
    return -1;
  p = strstr(cgroup, "/slurmstepd.scope/");
  if (p == NULL)
    return -1;
  p += strlen("/slurmstepd.scope/");
  slash = strchr(p, '/');
  if (slash == NULL)
    len = strlen(p);
  else
    len = (size_t)(slash - p);
  while (len > 0 && (p[len - 1] == '\n' || p[len - 1] == '\r'))
    len--;
  if (len == 0 || len >= token_cap)
    return -1;
  if (len == 6 && strncmp(p, "system", 6) == 0)
    return -1;
  memcpy(token_out, p, len);
  token_out[len] = '\0';
  return 0;
}

/*! Extract absolute cgroup path after "controllers:" (v2 `0::/path`). */
static int parse_cgroup_relpath(const char *cgroup, char *out, size_t out_cap)
{
  const char *p;
  const char *colon;
  size_t len;

  if (cgroup == NULL || out == NULL || out_cap == 0)
    return -1;
  colon = strchr(cgroup, ':');
  if (colon == NULL)
    return -1;
  colon = strchr(colon + 1, ':');
  if (colon == NULL)
    return -1;
  p = colon + 1;
  while (*p == ' ' || *p == '\t')
    p++;
  if (*p != '/')
    return -1;
  len = 0;
  while (p[len] != '\0' && p[len] != '\n' && p[len] != '\r')
    len++;
  while (len > 0 && p[len - 1] == '/')
    len--;
  if (len == 0 || len + 1 > out_cap)
    return -1;
  memcpy(out, p, len);
  out[len] = '\0';
  return 0;
}

static int read_cgroup_line(const char *proc_root, int pid, char *buf, size_t cap)
{
  char path[512];
  FILE *f;
  int n;

  n = snprintf(path, sizeof(path), "%s/%d/cgroup", proc_root, pid);
  if (n < 0 || (size_t)n >= sizeof(path))
    return -1;
  f = fopen(path, "re");
  if (f == NULL)
    return -1;
  if (fgets(buf, (int)cap, f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return 0;
}

static int read_environ_keys(const char *proc_root, int pid, char *jid_out, size_t jid_cap,
                             char *gpus_out, size_t gpus_cap, int *have_step_out)
{
  char path[512];
  FILE *f;
  char *data = NULL;
  size_t data_cap = 0;
  size_t data_len = 0;
  size_t off;
  int n;
  int saw_jid = 0;

  if (have_step_out)
    *have_step_out = 0;
  if (jid_out && jid_cap)
    jid_out[0] = '\0';
  if (gpus_out && gpus_cap)
    gpus_out[0] = '\0';

  n = snprintf(path, sizeof(path), "%s/%d/environ", proc_root, pid);
  if (n < 0 || (size_t)n >= sizeof(path))
    return -1;
  f = fopen(path, "re");
  if (f == NULL)
    return -1;
  for (;;) {
    char chunk[4096];
    size_t got = fread(chunk, 1, sizeof(chunk), f);

    if (got == 0)
      break;
    if (data_len + got + 1 > data_cap) {
      size_t ncap = data_cap ? data_cap * 2 : 8192;
      char *nd;

      while (ncap < data_len + got + 1)
        ncap *= 2;
      nd = realloc(data, ncap);
      if (nd == NULL) {
        free(data);
        fclose(f);
        return -1;
      }
      data = nd;
      data_cap = ncap;
    }
    memcpy(data + data_len, chunk, got);
    data_len += got;
  }
  fclose(f);
  if (data == NULL) {
    data = calloc(1, 1);
    if (data == NULL)
      return -1;
    data_len = 0;
  }
  data[data_len] = '\0';

  off = 0;
  while (off < data_len) {
    char *entry = data + off;
    size_t elen = strlen(entry);

    if (strncmp(entry, "SLURM_JOB_ID=", 13) == 0 && jid_out && jid_cap) {
      snprintf(jid_out, jid_cap, "%s", entry + 13);
      saw_jid = 1;
    } else if (strncmp(entry, "SLURM_JOB_GPUS=", 15) == 0 && gpus_out && gpus_cap) {
      snprintf(gpus_out, gpus_cap, "%s", entry + 15);
    } else if (strncmp(entry, "SLURM_STEP_ID=", 14) == 0 && have_step_out) {
      *have_step_out = 1;
    }
    off += elen + 1;
  }
  free(data);
  return saw_jid ? 0 : 1;
}

static int pid_uid_is_root(const char *proc_root, int pid)
{
  char path[512];
  FILE *f;
  char line[256];
  int n;

  n = snprintf(path, sizeof(path), "%s/%d/status", proc_root, pid);
  if (n < 0 || (size_t)n >= sizeof(path))
    return 1;
  f = fopen(path, "re");
  if (f == NULL)
    return 1;
  while (fgets(line, sizeof(line), f) != NULL) {
    unsigned uid;

    if (sscanf(line, "Uid: %u", &uid) == 1) {
      fclose(f);
      return uid == 0;
    }
  }
  fclose(f);
  return 1;
}

static int cmp_int(const void *a, const void *b)
{
  int x = *(const int *)a;
  int y = *(const int *)b;

  return (x > y) - (x < y);
}

static int cmp_jid_entry(const void *a, const void *b)
{
  const struct job_proc_jid_entry *x = a;
  const struct job_proc_jid_entry *y = b;

  return strcmp(x->jid, y->jid);
}

static struct scope_bucket *find_or_add_bucket(struct scope_bucket **arr, size_t *n, size_t *cap,
                                               const char *token)
{
  size_t i;
  struct scope_bucket *nb;

  for (i = 0; i < *n; i++) {
    if (strcmp((*arr)[i].token, token) == 0)
      return &(*arr)[i];
  }
  if (*n + 1 > *cap) {
    size_t ncap = *cap ? *cap * 2 : 8;

    nb = realloc(*arr, ncap * sizeof(**arr));
    if (nb == NULL)
      return NULL;
    *arr = nb;
    *cap = ncap;
  }
  nb = &(*arr)[*n];
  memset(nb, 0, sizeof(*nb));
  snprintf(nb->token, sizeof(nb->token), "%s", token);
  (*n)++;
  return nb;
}

int job_proc_jid_from_stepd_title(const char *title, size_t len, char *jid_out, size_t jid_cap)
{
  const size_t plen = sizeof(JOB_PROC_STEPD_TITLE_PREFIX) - 1;
  size_t i;

  if (title == NULL || jid_out == NULL || jid_cap == 0 || len <= plen ||
      memcmp(title, JOB_PROC_STEPD_TITLE_PREFIX, plen) != 0)
    return -1;
  for (i = plen; i < len && isdigit((unsigned char)title[i]); i++)
    ;
  /* Digits must be followed by the step separator ('.' step / ':' namespace). */
  if (i == plen || i >= len || (title[i] != '.' && title[i] != ':') || i - plen >= jid_cap)
    return -1;
  memcpy(jid_out, title + plen, i - plen);
  jid_out[i - plen] = '\0';
  return job_proc_jid_acceptable(jid_out) ? 0 : -1;
}

/*! First pid listed in a cgroup.procs file, or -1 (missing, empty, malformed). */
static int read_first_cgroup_pid(const char *path)
{
  FILE *f = fopen(path, "re");
  int pid = -1;

  if (f == NULL)
    return -1;
  if (fscanf(f, "%d", &pid) != 1 || pid <= 0)
    pid = -1;
  fclose(f);
  return pid;
}

/*! Name an unnamed bucket from the stepd title of its extern step (one bounded read each
 *  of cgroup.procs and /proc/<pid>/cmdline). Fail-open: any read/parse miss leaves it unnamed.
 *  Returns 0, or -1 on allocation failure. */
static int name_bucket_from_stepd_title(const char *proc_root, const char *scope_dir,
                                        struct scope_bucket *b)
{
  char path[JOB_PROC_SCOPE_MAX + JOB_PROC_TOKEN_MAX + 64];
  char title[JOB_PROC_CMDLINE_MAX];
  char jid[JOB_PROC_JID_MAX + 1];
  FILE *f;
  size_t got;
  int pid;
  int n;

  n = snprintf(path, sizeof(path), "%s/%s/step_extern/slurm/cgroup.procs", scope_dir, b->token);
  if (n < 0 || (size_t)n >= sizeof(path) || (pid = read_first_cgroup_pid(path)) < 0)
    return 0;
  n = snprintf(path, sizeof(path), "%s/%d/cmdline", proc_root, pid);
  if (n < 0 || (size_t)n >= sizeof(path) || (f = fopen(path, "re")) == NULL)
    return 0;
  got = fread(title, 1, sizeof(title), f);
  fclose(f);
  if (job_proc_jid_from_stepd_title(title, got, jid, sizeof(jid)) < 0)
    return 0;
  if (bucket_add_pid(b, pid) < 0)
    return -1;
  snprintf(b->jid, sizeof(b->jid), "%s", jid);
  if (b->cgroup_relpath[0] == '\0')
    snprintf(b->cgroup_relpath, sizeof(b->cgroup_relpath),
             JOB_PROC_STEPD_SCOPE_REL "/%s/step_extern/slurm", b->token);
  b->named = 1;
  b->named_from_title = 1;
  return 0;
}

/*! Visit every job token under <cgroup_root>/slurmstepd.scope; name tokens that no job
 *  process named from their root stepd title. Root pid dirs are skipped by the /proc walk
 *  (proc_filter), so an idle allocation is only visible here. Sets *any_scope when a job
 *  token exists. Returns 0, or -1 on allocation failure. */
static int name_unnamed_scopes_from_stepd_titles(const char *proc_root,
                                                 struct scope_bucket **buckets, size_t *nbuckets,
                                                 size_t *bcap, int *any_scope)
{
  char scope_dir[JOB_PROC_SCOPE_MAX];
  struct dirent **tokens = NULL;
  int ntok;
  int i;
  int rc = 0;
  int n;

  n = snprintf(scope_dir, sizeof(scope_dir), "%s" JOB_PROC_STEPD_SCOPE_REL,
               job_cgroup_mem_cgroup_root());
  if (n < 0 || (size_t)n >= sizeof(scope_dir))
    return 0;
  ntok = scandir(scope_dir, &tokens, NULL, alphasort);
  if (ntok < 0)
    return 0; /* no slurmstepd.scope on this node: fail-open */
  for (i = 0; i < ntok; i++) {
    const char *name = tokens[i]->d_name;
    struct scope_bucket *b;

    if (rc == 0 && name[0] != '.' && strcmp(name, "system") != 0 &&
        strlen(name) < JOB_PROC_TOKEN_MAX &&
        (tokens[i]->d_type == DT_DIR || tokens[i]->d_type == DT_UNKNOWN)) {
      *any_scope = 1;
      b = find_or_add_bucket(buckets, nbuckets, bcap, name);
      if (b == NULL || (!b->named && name_bucket_from_stepd_title(proc_root, scope_dir, b) < 0))
        rc = -1;
    }
    free(tokens[i]);
  }
  free(tokens);
  return rc;
}

static int merge_named_into_result(struct job_proc_discover_result *out, struct scope_bucket *b)
{
  size_t i;
  struct job_proc_jid_entry *e = NULL;

  if (!job_proc_jid_acceptable(b->jid))
    return 0;
  for (i = 0; i < out->n; i++) {
    if (strcmp(out->entries[i].jid, b->jid) == 0) {
      e = &out->entries[i];
      break;
    }
  }
  if (e == NULL) {
    if (out->n >= JOB_PROC_MAX_JIDS)
      return 0;
    e = &out->entries[out->n++];
    memset(e, 0, sizeof(*e));
    snprintf(e->jid, sizeof(e->jid), "%s", b->jid);
    if (b->job_gpus[0])
      snprintf(e->job_gpus, sizeof(e->job_gpus), "%s", b->job_gpus);
    if (b->cgroup_relpath[0])
      snprintf(e->cgroup_relpath, sizeof(e->cgroup_relpath), "%s", b->cgroup_relpath);
    e->named_from_title = b->named_from_title;
  } else {
    e->named_from_title = e->named_from_title && b->named_from_title;
    if (e->job_gpus[0] == '\0' && b->job_gpus[0])
      snprintf(e->job_gpus, sizeof(e->job_gpus), "%s", b->job_gpus);
    if (e->cgroup_relpath[0] == '\0' && b->cgroup_relpath[0])
      snprintf(e->cgroup_relpath, sizeof(e->cgroup_relpath), "%s", b->cgroup_relpath);
  }
  for (i = 0; i < b->npids; i++) {
    size_t j;
    int *np;
    int found = 0;

    for (j = 0; j < e->npids; j++) {
      if (e->pids[j] == b->pids[i]) {
        found = 1;
        break;
      }
    }
    if (found)
      continue;
    np = realloc(e->pids, (e->npids + 1) * sizeof(*np));
    if (np == NULL)
      return -1;
    e->pids = np;
    e->pids[e->npids++] = b->pids[i];
  }
  return 0;
}

int job_proc_discover(struct job_proc_discover_result *out, int require_step_id, int pid_cap)
{
  const char *proc_root;
  struct dirent **namelist = NULL;
  int n;
  int i;
  int walked = 0;
  int any_scope = 0;
  struct scope_bucket *buckets = NULL;
  size_t nbuckets = 0;
  size_t bcap = 0;
  struct job_proc_discover_result scratch;

  if (out == NULL)
    return -1;
  if (pid_cap <= 0)
    pid_cap = 4096;

  proc_root = job_proc_discover_proc_root();
  memset(&scratch, 0, sizeof(scratch));

  n = scandir(proc_root, &namelist, NULL, alphasort);
  if (n < 0) {
    if (!g_discover_warned) {
      monitor_log_warn("job_proc_discover: scandir(%s) failed: %m (fail-open)\n", proc_root);
      g_discover_warned = 1;
    }
    return 0;
  }

  for (i = 0; i < n && walked < pid_cap; i++) {
    struct dirent *de = namelist[i];
    int pid;
    char cgroup[JOB_PROC_SCOPE_MAX];
    char token[JOB_PROC_TOKEN_MAX];
    struct scope_bucket *b;
    char jid[JOB_PROC_JID_MAX + 1];
    char gpus[JOB_PROC_GPUS_ENV_MAX];
    int have_step = 0;
    int env_rc;

    if (!proc_filter_pid_at_root(proc_root, de))
      continue;
    pid = atoi(de->d_name);
    if (pid <= 0)
      continue;
    walked++;

    if (read_cgroup_line(proc_root, pid, cgroup, sizeof(cgroup)) < 0)
      continue;
    if (parse_scope_token(cgroup, token, sizeof(token)) == 0) {
      char rel[JOB_PROC_CGROUP_RELPATH_MAX];
      int root = pid_uid_is_root(proc_root, pid);

      any_scope = 1;
      b = find_or_add_bucket(&buckets, &nbuckets, &bcap, token);
      if (b == NULL)
        goto oom;
      /* Prefer non-root (job) leaf path; first scope pid may be root stepd. */
      if (parse_cgroup_relpath(cgroup, rel, sizeof(rel)) == 0 &&
          (b->cgroup_relpath[0] == '\0' || !root))
        snprintf(b->cgroup_relpath, sizeof(b->cgroup_relpath), "%s", rel);
      if (bucket_add_pid(b, pid) < 0)
        goto oom;
      if (root)
        continue;
      env_rc = read_environ_keys(proc_root, pid, jid, sizeof(jid), gpus, sizeof(gpus), &have_step);
      if (env_rc == 0 && !b->named && job_proc_jid_acceptable(jid)) {
        snprintf(b->jid, sizeof(b->jid), "%s", jid);
        if (gpus[0])
          snprintf(b->job_gpus, sizeof(b->job_gpus), "%s", gpus);
        b->named = 1;
      }
      if (have_step)
        b->have_step = 1;
    }
  }

  if (name_unnamed_scopes_from_stepd_titles(proc_root, &buckets, &nbuckets, &bcap, &any_scope) < 0)
    goto oom;

  if (!any_scope) {
    /* Fallback: no slurmstepd.scope on node — per-pid environ SLURM_JOB_ID. */
    for (i = 0; i < n && walked < pid_cap * 2; i++) {
      struct dirent *de = namelist[i];
      int pid;
      char jid[JOB_PROC_JID_MAX + 1];
      char gpus[JOB_PROC_GPUS_ENV_MAX];
      int have_step = 0;
      struct scope_bucket fake;

      if (!proc_filter_pid_at_root(proc_root, de))
        continue;
      pid = atoi(de->d_name);
      if (pid <= 0)
        continue;
      if (pid_uid_is_root(proc_root, pid))
        continue;
      if (read_environ_keys(proc_root, pid, jid, sizeof(jid), gpus, sizeof(gpus), &have_step) != 0)
        continue;
      if (!job_proc_jid_acceptable(jid))
        continue;
      if (require_step_id && !have_step)
        continue;
      memset(&fake, 0, sizeof(fake));
      snprintf(fake.jid, sizeof(fake.jid), "%s", jid);
      if (gpus[0])
        snprintf(fake.job_gpus, sizeof(fake.job_gpus), "%s", gpus);
      fake.pids = &pid;
      fake.npids = 1;
      fake.named = 1;
      if (merge_named_into_result(&scratch, &fake) < 0)
        goto oom;
    }
  } else {
    size_t bi;

    for (bi = 0; bi < nbuckets; bi++) {
      struct scope_bucket *b = &buckets[bi];

      if (!b->named)
        continue;
      if (require_step_id && !b->have_step && !b->named_from_title)
        continue;
      if (merge_named_into_result(&scratch, b) < 0)
        goto oom;
    }
  }

  for (i = 0; i < n; i++)
    free(namelist[i]);
  free(namelist);
  for (i = 0; (size_t)i < nbuckets; i++)
    free(buckets[i].pids);
  free(buckets);

  {
    size_t j;

    for (j = 0; j < scratch.n; j++)
      qsort(scratch.entries[j].pids, scratch.entries[j].npids, sizeof(int), cmp_int);
    qsort(scratch.entries, scratch.n, sizeof(scratch.entries[0]), cmp_jid_entry);
  }

  job_proc_discover_result_clear(out);
  *out = scratch;
  return 0;

oom:
  for (i = 0; i < n; i++)
    free(namelist[i]);
  free(namelist);
  for (i = 0; (size_t)i < nbuckets; i++)
    free(buckets[i].pids);
  free(buckets);
  job_proc_discover_result_clear(&scratch);
  return -1;
}
