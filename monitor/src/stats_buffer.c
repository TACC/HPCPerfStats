/* Stats buffer orchestration, ring buffer, and payload collect/write. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <errno.h>
#include <time.h>
#include <search.h>
#include <stdint.h>

#include "stats.h"
#include "collect.h"
#include "collect_tier.h"
#include "fileio.h"
#include "path_open_fail_once.h"
#include "stats_buffer.h"
#include "stats_buffer_data_append.h"
#include "stats_buffer_rows.h"
#include "stats_buffer_rmq_internal.h"
#include "stats_buffer_uts.h"
#include "stats_text_format.h"
#if defined(__has_include)
#if __has_include("monitor_capability_slug.h")
#include "monitor_capability_slug.h"
#endif
#endif
#ifndef MONITOR_CAPABILITY_SLUG
#define MONITOR_CAPABILITY_SLUG ""
#endif
#include "trace.h"
#include "monitor_log.h"
#include "pscanf.h"
#include "string1.h"

#define SF_SCHEMA_CHAR '!'
#define SF_DEVICES_CHAR '@'
#define SF_COMMENT_CHAR '#'
#define SF_PROPERTY_CHAR '$'
#define SF_MARK_CHAR '%'

static long stats_buffer_monotonic_us(void)
{
  struct timespec ts;

  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  return (long)ts.tv_sec * 1000000L + (long)ts.tv_nsec / 1000L;
}

static void stats_buf_emit(void *opaque, const char *fmt, ...)
{
  struct stats_buffer *sf = opaque;
  va_list ap;

  va_start(ap, fmt);
  if (stats_buffer_data_append_vfmt(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, fmt, ap) <
      0) {
    /* Best-effort on OOM (buffer unchanged). */
  }
  va_end(ap);
}

void stats_buffer_runtime_caches_reset(void)
{
  stats_buffer_rmq_shutdown();
  stats_buffer_uts_cache_reset();
  cpu_stats_invalidate_file_caches();
  net_stats_invalidate_iface_cache();
}

static int stats_buffer_is_schema_payload(const struct stats_buffer *sf);

static int stats_buffer_is_schema_payload(const struct stats_buffer *sf)
{
  const char *p;

  if (sf == NULL || sf->sf_data == NULL)
    return 0;

  p = sf->sf_data;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  return *p == SF_PROPERTY_CHAR;
}

int stats_wr_hdr(struct stats_buffer *sf)
{
  unsigned long long uptime = 0;

  const struct utsname *uts = stats_buffer_cached_uts();
  pscanf("/proc/uptime", "%llu", &uptime);

  stats_format_emit_property_banner(stats_buf_emit, sf, SF_PROPERTY_CHAR, STATS_PROGRAM,
                                    STATS_VERSION, uts->nodename, uts->sysname, uts->machine,
                                    uts->release, uts->version, uptime, MONITOR_CAPABILITY_SLUG);

  {
    size_t i = 0;
    struct stats_type *type;

    while ((type = stats_type_for_each(&i)) != NULL) {
      if (!type->st_enabled)
        continue;

      TRACE("type %s, schema_len %zu\n", type->st_name, type->st_schema.sc_len);
      stats_format_emit_schema_line(stats_buf_emit, sf, SF_SCHEMA_CHAR, type);
    }
  }

  return 0;
}

int stats_buffer_open(struct stats_buffer *sf, const char *host, const char *port,
                      const char *queue, const char *user, const char *password)
{
  int rc = 0;
  memset(sf, 0, sizeof(*sf));
  if (host == NULL || port == NULL || queue == NULL || user == NULL || password == NULL)
    return -1;
  sf->sf_data = strdup("");
  if (sf->sf_data == NULL)
    return -1;
  sf->sf_data_len = 0;
  sf->sf_data_cap = 1;
  sf->sf_host = strdup(host);
  sf->sf_port = strdup(port);
  sf->sf_queue = strdup(queue);
  sf->sf_user = strdup(user);
  sf->sf_password = strdup(password);
  if (sf->sf_host == NULL || sf->sf_port == NULL || sf->sf_queue == NULL || sf->sf_user == NULL ||
      sf->sf_password == NULL) {
    stats_buffer_close(sf);
    return -1;
  }
  /* Strip whitespace/tabs so getaddrinfo sees a valid host (config/CLI copy-paste). */
  str_trim_inplace(sf->sf_host);
  str_trim_inplace(sf->sf_port);
  str_trim_inplace(sf->sf_queue);
  str_trim_inplace(sf->sf_user);
  if (sf->sf_host[0] == '\0' || sf->sf_port[0] == '\0' || sf->sf_queue[0] == '\0' ||
      sf->sf_user[0] == '\0') {
    stats_buffer_close(sf);
    return -1;
  }

  return rc;
}

int stats_buffer_close(struct stats_buffer *sf)
{
  int rc = 0;

  free(sf->sf_data);
  free(sf->sf_host);
  free(sf->sf_port);
  free(sf->sf_queue);
  free(sf->sf_user);
  free(sf->sf_password);
  free(sf->sf_mark);
  memset(sf, 0, sizeof(*sf));
  return rc;
}

int stats_buffer_mark(struct stats_buffer *sf, const char *fmt, ...)
{
  va_list args;
  va_start(args, fmt);
  (void)stats_format_append_mark_va(&sf->sf_mark, fmt, args);
  va_end(args);
  return 0;
}

static void stats_buffer_append_mark_lines(struct stats_buffer *sf)
{
  if (sf->sf_mark == NULL)
    return;

  stats_format_emit_mark_multiline(stats_buf_emit, sf, SF_MARK_CHAR, sf->sf_mark);
}

/* Choose the sample-row tier for this payload. The `$`-always-full rule is
 * enforced via stats_buffer_is_schema_payload() so schema/rotation messages
 * never go sparse. */
static enum stats_row_tier stats_buffer_collect_row_tier(const struct stats_buffer *sf)
{
  return stats_buffer_row_tier_decide(stats_buffer_is_schema_payload(sf), collect_tier_enabled(),
                                      (int)collect_tier_get_phase());
}

enum stats_row_tier stats_buffer_payload_row_tier(const struct stats_buffer *sf)
{
  return stats_buffer_row_tier_decide(stats_buffer_is_schema_payload(sf), collect_tier_enabled(),
                                      (int)collect_tier_get_phase());
}

int stats_buffer_collect(struct stats_buffer *sf)
{
  int rc = 0;
  char header[256];
  int header_len;
  struct timespec time;

#ifdef STATS_BUFFER_TEST_TIME_HOOK
  time.tv_sec = 1234567890;
  time.tv_nsec = 0;
#else
  if (clock_gettime(CLOCK_REALTIME, &time) != 0) {
    monitor_log_error("cannot clock_gettime(): %m\n");
    rc = -1;
    goto out;
  }
#endif
  header_len = snprintf(header, sizeof(header), "\n%f %s %s\n", time.tv_sec + 1e-9 * time.tv_nsec,
                        jobid, stats_buffer_cached_uts()->nodename);
  if (header_len < 0 || (size_t)header_len >= sizeof(header) ||
      stats_buffer_data_append_bytes(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, header,
                                     (size_t)header_len) < 0) {
    rc = -1;
    goto out;
  }

  stats_buffer_append_mark_lines(sf);
  stats_buffer_append_enabled_type_rows(sf, stats_buffer_collect_row_tier(sf));
out:
  return rc;
}

int stats_buffer_write(struct stats_buffer *sf)
{
  int rc = stats_buffer_collect(sf);
  if (rc < 0)
    return rc;
  rc = stats_buffer_send_payload(sf);
  return rc;
}

int stats_buffer_resend(struct stats_buffer *sf)
{
  return stats_buffer_send_payload(sf);
}

int ring_buffer_insert(struct stats_buffer *sf, struct sf_ring_buffer *w, int max_buffer_size,
                       int allow_ring_buffer_overwrite)
{
  int rc = 0;
  struct sf_queue *q_new;
  struct sf_queue *victim = NULL;

  /* Case 1: Empty buffer */
  if (w->q_count == 0) {
    q_new = (struct sf_queue *)calloc(1, sizeof(struct sf_queue));
    if (q_new == NULL) {
      rc = -1;
      goto out;
    }
    q_new->sf = sf;
    q_new->forward = q_new;
    q_new->backward = q_new;
    insque(q_new, q_new);
    w->q = q_new;
    w->q_first = w->q;
    w->q_count += 1;
    goto out;
  }

  /* Case 2: Full buffer */
  if (w->q_count >= max_buffer_size && max_buffer_size != -1) {
    if (!allow_ring_buffer_overwrite) {
      rc = -1;
      goto out;
    }
    {
      victim = w->q->forward;
      struct sf_queue *node = w->q_first;
      int scanned = 0;

      while (node != NULL && scanned < w->q_count) {
        if (node->sf != NULL && !stats_buffer_is_schema_payload(node->sf)) {
          victim = node;
          break;
        }
        node = node->forward;
        scanned++;
        if (node == w->q_first)
          break;
      }

      if (victim->sf != NULL && stats_buffer_is_schema_payload(victim->sf)) {
        rc = -1;
        goto out;
      }

      if (victim->sf != NULL) {
        stats_buffer_close(victim->sf);
        free(victim->sf);
      }
      victim->sf = sf;
    }
    w->q = victim;
    w->q_first = w->q->forward;
    w->d_count += 1;
    goto out;
  }

  /* Case 3: Otherwise */
  q_new = (struct sf_queue *)calloc(1, sizeof(struct sf_queue));
  if (q_new == NULL) {
    rc = -1;
    goto out;
  }
  q_new->sf = sf;
  insque(q_new, w->q);
  w->q = q_new;
  w->q_count += 1;

out:
  return rc;
}

static void ring_buffer_drop_first(struct sf_ring_buffer *w)
{
  struct sf_queue *sf = w->q_first;

  if (sf == NULL || w->q_count <= 0)
    return;

  if (w->q_count == 1) {
    stats_buffer_close(sf->sf);
    free(sf->sf);
    remque(sf);
    free(sf);
    w->q = NULL;
    w->q_first = NULL;
    w->q_count = 0;
    return;
  }

  w->q_first = sf->forward;
  if (w->q == sf)
    w->q = sf->backward;

  stats_buffer_close(sf->sf);
  free(sf->sf);
  remque(sf);
  free(sf);
  w->q_count -= 1;
}

static size_t g_rmq_soft_max_override; /* 0 = use STATS_BUFFER_RMQ_SOFT_MAX_BYTES */

size_t stats_buffer_rmq_soft_max_bytes(void)
{
  if (g_rmq_soft_max_override != 0)
    return g_rmq_soft_max_override;
  return STATS_BUFFER_RMQ_SOFT_MAX_BYTES;
}

int stats_buffer_rmq_batch_can_add(size_t batch_len, int batch_count, size_t next_len,
                                   size_t soft_max)
{
  size_t new_len;

  /* First sample always allowed (single-entry overshoot is intentional). */
  if (batch_count <= 0)
    return 1;
  if (soft_max == 0)
    return 1;
  if (next_len > SIZE_MAX - batch_len)
    return 0;
  new_len = batch_len + next_len;
  return new_len <= soft_max;
}

#if defined(STATS_BUFFER_TEST_SEND_HOOK)
void stats_buffer_rmq_test_set_soft_max_bytes(size_t n)
{
  g_rmq_soft_max_override = n;
}
#endif

void ring_buffer_resend_limited(struct sf_ring_buffer *w, int max_batches, long max_runtime_us,
                                int *processed_entries)
{
  enum { max_batch_entries = 10 };
  int batches = 0;
  long started_us = -1;

  if (processed_entries != NULL)
    *processed_entries = 0;

  if (max_runtime_us > 0)
    started_us = stats_buffer_monotonic_us();

  while (w->q_count > 0) {
    struct sf_queue *head = w->q_first;
    struct sf_queue *node;
    size_t batch_len = 1;
    int batch_count = 0;

    if (head == NULL || head->sf == NULL)
      break;

    /* Keep schema/header payloads isolated: listend.py treats `$` specially. */
    if (stats_buffer_is_schema_payload(head->sf)) {
      w->status = stats_buffer_resend(head->sf);
      if (w->status != 0)
        break;
      w->r_count++;
      if (processed_entries != NULL)
        (*processed_entries)++;
      ring_buffer_drop_first(w);
      batches++;
      goto maybe_budget_break;
    }

    node = head;
    while (batch_count < w->q_count && batch_count < max_batch_entries) {
      if (node == NULL || node->sf == NULL || node->sf->sf_data == NULL)
        break;
      if (stats_buffer_is_schema_payload(node->sf))
        break;
      if (!stats_buffer_rmq_batch_can_add(batch_len, batch_count, node->sf->sf_data_len,
                                          stats_buffer_rmq_soft_max_bytes()))
        break;
      batch_len += node->sf->sf_data_len;
      batch_count++;
      node = node->forward;
      if (node == head)
        break;
    }

    if (batch_count <= 0)
      break;

    if (batch_count == 1) {
      w->status = stats_buffer_resend(head->sf);
    } else {
      char *merged = (char *)malloc(batch_len);
      struct stats_buffer merged_sf;

      if (merged == NULL) {
        w->status = -1;
        ERROR("ring_buffer_resend: merged batch malloc failed (%zu bytes, batch=%d)\n", batch_len,
              batch_count);
        break;
      }
      char *dst = merged;
      node = head;
      for (int i = 0; i < batch_count; i++) {
        size_t n = node->sf->sf_data_len;
        memcpy(dst, node->sf->sf_data, n);
        dst += n;
        node = node->forward;
      }
      *dst = '\0';

      merged_sf = *head->sf;
      merged_sf.sf_data = merged;
      merged_sf.sf_data_len = (size_t)(dst - merged);
      w->status = stats_buffer_send_payload(&merged_sf);
      free(merged);
    }

    if (w->status != 0)
      break;

    w->r_count += batch_count;
    if (processed_entries != NULL)
      *processed_entries += batch_count;
    for (int i = 0; i < batch_count; i++)
      ring_buffer_drop_first(w);
    batches++;

  maybe_budget_break:
    if (max_batches > 0 && batches >= max_batches)
      break;
    if (max_runtime_us > 0 && started_us > 0) {
      long now_us = stats_buffer_monotonic_us();
      if (now_us > 0 && now_us - started_us >= max_runtime_us)
        break;
    }
  }
}

void ring_buffer_resend(struct sf_ring_buffer *w)
{
  ring_buffer_resend_limited(w, -1, -1, NULL);
}

int stats_buffer_write_file(struct stats_buffer *sf, char *path)
{
  int rc = 0;
  FILE *sf_file = path_file_fopen_append(path);
  if (sf_file == NULL) {
    rc = -1;
    goto out;
  }

  fseek(sf_file, 0, SEEK_END);
  fprintf(sf_file, "%s", sf->sf_data);

  if (ferror(sf_file)) {
    ERROR("error writing to `%s': %m\n", path);
    rc = -1;
  }
out:
  if (sf_file != NULL)
    fclose(sf_file);
  return rc;
}

int ring_buffer_load_file(FILE *sf_file, struct sf_ring_buffer *w, const char *host,
                          const char *port, const char *queue, const char *user,
                          const char *password, int max_buffer_size,
                          int allow_ring_buffer_overwrite)
{
  int n_stats = 0;
  int stats_start = 0;
  int rc = 0;
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  struct stats_buffer *sf;
  sf = (struct stats_buffer *)malloc(sizeof(*sf));
  if (sf == NULL) {
    rc = -1;
    goto out;
  }
  if (sf_file == NULL) {
    rc = -1;
    free(sf);
    goto out;
  }
  if (stats_buffer_open(sf, host, port, queue, user, password) < 0) {
    TRACE("Failed opening data buffer : %m\n");
    rc = -1;
    free(sf);
    goto out;
  }
  while (getline(&line_buf, &line_buf_size, sf_file) != -1) {
    if (line_buf[0] == '\n' && stats_start == 0)
      continue;
    if (line_buf[0] != '\n') {
      if (stats_buffer_data_append_fmt(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, "%s",
                                       line_buf) < 0) {
        /* Best-effort on OOM. */
      }
      if (stats_start == 0)
        stats_start = 1;
    } else {
      n_stats++;
      rc = ring_buffer_insert(sf, w, -1, allow_ring_buffer_overwrite);
      sf = (struct stats_buffer *)malloc(sizeof(struct stats_buffer));
      if (sf == NULL) {
        TRACE("Failed allocating data buffer : %m\n");
        rc = -1;
        goto out;
      }
      if (stats_buffer_open(sf, host, port, queue, user, password) < 0 || rc < 0) {
        TRACE("Failed inserting data to buffer : %m\n");
        stats_buffer_close(sf);
        free(sf);
        rc = -1;
        goto out;
      }
    }
  }
  rc = ring_buffer_insert(sf, w, -1, allow_ring_buffer_overwrite);
  if (rc < 0) {
    stats_buffer_close(sf);
    free(sf);
    goto out;
  }
  n_stats++;
  w->l_count += n_stats;
  TRACE("Loaded %d stats from dumpfile\n", n_stats);

out:
  free(line_buf);
  return rc;
}
