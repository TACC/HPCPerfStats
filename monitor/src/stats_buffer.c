#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <errno.h>
#include <sys/utsname.h>
#include <syslog.h>
#include <search.h>
#include <time.h>
#include <rabbitmq-c/amqp.h>
#include <rabbitmq-c/framing.h>
#include <rabbitmq-c/tcp_socket.h>

#include "stats.h"
#include "collect.h"
#include "fileio.h"
#include "stats_buffer.h"
#include "stats_buffer_data_append.h"
#include "schema.h"
#include "trace.h"
#include "pscanf.h"
#include "string1.h"

/* Sample interval from monitor_daemon.c (conf `freq`); pace RMQ TCP reconnect attempts. */
extern double freq;

#define SF_SCHEMA_CHAR '!'
#define SF_DEVICES_CHAR '@'
#define SF_COMMENT_CHAR '#'
#define SF_PROPERTY_CHAR '$'
#define SF_MARK_CHAR '%'
#define RMQ_EXCHANGE "amq.direct"
#define RMQ_VHOST "/"
#define RMQ_CHANNEL 1

#ifdef DEBUG
/* Decode rabbitmq-c failures for DEBUG builds (syslog via ERROR when RABBITMQ). */
static void rmq_debug_log_amqp_status(const char *ctx, int st)
{
  int save_e = errno;
  const char *s = amqp_error_string2(st);

  ERROR("%s: %s (%d)", ctx, s != NULL ? s : "?", st);
  if (st == AMQP_STATUS_SOCKET_ERROR || st == AMQP_STATUS_TCP_ERROR)
    ERROR("%s: errno=%d (%s)", ctx, save_e, strerror(save_e));
}

static void rmq_debug_append_reply_text(char *buf, size_t buflen, const amqp_bytes_t *text)
{
  if (buflen == 0)
    return;
  buf[0] = '\0';
  if (text == NULL || text->bytes == NULL || text->len == 0)
    return;
  size_t n = text->len < buflen - 1 ? text->len : buflen - 1;
  memcpy(buf, text->bytes, n);
  buf[n] = '\0';
}

static void rmq_debug_log_rpc_reply(const char *ctx, amqp_rpc_reply_t r)
{
  const char *mn;

  switch (r.reply_type) {
  case AMQP_RESPONSE_NORMAL:
    ERROR("%s: unexpected AMQP_RESPONSE_NORMAL in error path", ctx);
    return;
  case AMQP_RESPONSE_NONE:
    ERROR("%s: AMQP_RESPONSE_NONE (unexpected EOF or incomplete read from broker)", ctx);
    return;
  case AMQP_RESPONSE_LIBRARY_EXCEPTION:
    rmq_debug_log_amqp_status(ctx, r.library_error);
    return;
  case AMQP_RESPONSE_SERVER_EXCEPTION:
    mn = amqp_method_name(r.reply.id);
    ERROR("%s: broker error method=%s id=0x%x", ctx, mn != NULL ? mn : "?", (unsigned)r.reply.id);
    if (r.reply.id == AMQP_CONNECTION_CLOSE_METHOD && r.reply.decoded != NULL) {
      amqp_connection_close_t *m = (amqp_connection_close_t *)r.reply.decoded;
      char textbuf[256];

      rmq_debug_append_reply_text(textbuf, sizeof(textbuf), &m->reply_text);
      ERROR("%s: connection.close reply_code=%u class_id=%u method_id=%u text=%s", ctx,
	    (unsigned)m->reply_code, (unsigned)m->class_id, (unsigned)m->method_id, textbuf);
    } else if (r.reply.id == AMQP_CHANNEL_CLOSE_METHOD && r.reply.decoded != NULL) {
      amqp_channel_close_t *m = (amqp_channel_close_t *)r.reply.decoded;
      char textbuf[256];

      rmq_debug_append_reply_text(textbuf, sizeof(textbuf), &m->reply_text);
      ERROR("%s: channel.close reply_code=%u class_id=%u method_id=%u text=%s", ctx,
	    (unsigned)m->reply_code, (unsigned)m->class_id, (unsigned)m->method_id, textbuf);
    }
    return;
  default:
    ERROR("%s: unknown reply_type=%d", ctx, (int)r.reply_type);
    return;
  }
}
#endif /* DEBUG */

/* One AMQP connection per process (libev single-threaded). Reconnect on credential mismatch or I/O failure.
 * When the broker is down, allow at most one new TCP connect attempt per conf sample interval (`freq`),
 * so ring-buffer resend loops in one libev tick do not storm the network. */
static amqp_connection_state_t rmq_conn;
static int rmq_channel_open;
static struct timespec rmq_backoff_until;
static int rmq_backoff_until_valid;

static void rmq_clear_connect_backoff(void)
{
  rmq_backoff_until_valid = 0;
}

static int rmq_connect_backoff_active(void)
{
  struct timespec now;

  if (!rmq_backoff_until_valid)
    return 0;
  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
    return 0;
  if (now.tv_sec < rmq_backoff_until.tv_sec
      || (now.tv_sec == rmq_backoff_until.tv_sec && now.tv_nsec < rmq_backoff_until.tv_nsec))
    return 1;
  rmq_backoff_until_valid = 0;
  return 0;
}

static void rmq_arm_connect_backoff(void)
{
  struct timespec now;
  double f = freq;

  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
    return;
  if (f <= 0.0)
    f = 1.0;
  {
    time_t add_sec = (time_t)f;
    double frac = f - (double)add_sec;

    if (frac < 0.0)
      frac = 0.0;
    long add_nsec = (long)(frac * 1e9);
    if (add_nsec >= 1000000000L)
      add_nsec = 999999999L;
    rmq_backoff_until.tv_sec = now.tv_sec + add_sec;
    rmq_backoff_until.tv_nsec = now.tv_nsec + add_nsec;
    if (rmq_backoff_until.tv_nsec >= 1000000000L) {
      rmq_backoff_until.tv_sec++;
      rmq_backoff_until.tv_nsec -= 1000000000L;
    }
  }
  rmq_backoff_until_valid = 1;
}

static char *rmq_stored_host;
static char *rmq_stored_port;
static char *rmq_stored_user;
static char *rmq_stored_pass;
static char *rmq_declared_queue;

/* Tier B: one uname(2) per cache epoch (invalidated on SIGHUP via runtime_caches_reset). */
static struct utsname cached_uts;
static int cached_uts_valid;

static void stats_buffer_ensure_uts_cached(void)
{
  if (cached_uts_valid)
    return;
  uname(&cached_uts);
  cached_uts_valid = 1;
}

static char *row_line_buf;
static size_t row_line_cap;

static int rmq_open_tcp_and_login(amqp_connection_state_t conn, struct stats_buffer *sf,
				  amqp_socket_t **socket_out, int *channel_opened_out);
static int rmq_declare_queue_and_bind_to_exchange(amqp_connection_state_t conn, struct stats_buffer *sf);

/* Payload assembly uses stats_buffer_data_append.c (incremental realloc; unit-tested). */
static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
  __attribute__((format(printf, 2, 3)));

static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
{
  va_list ap;
  va_start(ap, fmt);
  if (stats_buffer_data_append_vfmt(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, fmt, ap) < 0) {
    /* Best-effort on OOM (buffer unchanged). */
  }
  va_end(ap);
}

static void stats_buffer_append_schema_entry_suffix(struct stats_buffer *sf, struct schema_entry *se)
{
  if (se->se_type == SE_CONTROL)
    stats_buffer_append_fmt(sf, ",C");
  if (se->se_type == SE_EVENT)
    stats_buffer_append_fmt(sf, ",E");
  if (se->se_unit != NULL)
    stats_buffer_append_fmt(sf, ",U=%s", se->se_unit);
  if (se->se_width != 0)
    stats_buffer_append_fmt(sf, ",W=%u", se->se_width);
}

static void stats_buffer_append_schema_line_for_type(struct stats_buffer *sf, struct stats_type *type)
{
  stats_buffer_append_fmt(sf, "%c%s", SF_SCHEMA_CHAR, type->st_name);
  for (size_t j = 0; j < type->st_schema.sc_len; j++) {
    struct schema_entry *se = type->st_schema.sc_ent[j];
    stats_buffer_append_fmt(sf, " %s", se->se_key);
    stats_buffer_append_schema_entry_suffix(sf, se);
  }
  stats_buffer_append_fmt(sf, "\n");
}

static void close_rmq_connection(amqp_connection_state_t conn, int channel_opened)
{
  if (conn == NULL)
    return;
  if (channel_opened) {
    amqp_channel_close(conn, RMQ_CHANNEL, AMQP_REPLY_SUCCESS);
    amqp_connection_close(conn, AMQP_REPLY_SUCCESS);
  }
  amqp_destroy_connection(conn);
}

static void rmq_stored_free(void)
{
  free(rmq_stored_host);
  free(rmq_stored_port);
  free(rmq_stored_user);
  free(rmq_stored_pass);
  rmq_stored_host = NULL;
  rmq_stored_port = NULL;
  rmq_stored_user = NULL;
  rmq_stored_pass = NULL;
}

static int rmq_stored_matches(struct stats_buffer *sf)
{
  if (rmq_stored_host == NULL)
    return 0;
  return strcmp(rmq_stored_host, sf->sf_host) == 0 && strcmp(rmq_stored_port, sf->sf_port) == 0
	 && strcmp(rmq_stored_user, sf->sf_user) == 0 && strcmp(rmq_stored_pass, sf->sf_password) == 0;
}

static int rmq_stored_save(struct stats_buffer *sf)
{
  rmq_stored_free();
  rmq_stored_host = strdup(sf->sf_host);
  rmq_stored_port = strdup(sf->sf_port);
  rmq_stored_user = strdup(sf->sf_user);
  rmq_stored_pass = strdup(sf->sf_password);
  if (rmq_stored_host == NULL || rmq_stored_port == NULL || rmq_stored_user == NULL || rmq_stored_pass == NULL) {
    rmq_stored_free();
    return -1;
  }
  return 0;
}

static void rmq_soft_disconnect(void)
{
  if (rmq_conn != NULL) {
    close_rmq_connection(rmq_conn, rmq_channel_open);
    rmq_conn = NULL;
  }
  rmq_channel_open = 0;
  free(rmq_declared_queue);
  rmq_declared_queue = NULL;
}

void stats_buffer_rmq_shutdown(void)
{
  rmq_soft_disconnect();
  rmq_stored_free();
  rmq_clear_connect_backoff();
}

void stats_buffer_runtime_caches_reset(void)
{
  stats_buffer_rmq_shutdown();
  cached_uts_valid = 0;
  cpu_stats_invalidate_file_caches();
  net_stats_invalidate_iface_cache();
}

static int rmq_ensure_connected(struct stats_buffer *sf)
{
  if (rmq_conn != NULL && rmq_stored_matches(sf) && rmq_channel_open)
    return 0;

  if (rmq_connect_backoff_active()) {
#ifdef DEBUG
    ERROR("RMQ: connect backoff active, skipping connect attempt (see conf freq)");
#endif
    return -1;
  }

  rmq_soft_disconnect();

  if (!rmq_stored_matches(sf))
    rmq_stored_free();

#ifdef DEBUG
  ERROR("RMQ: connecting to %s:%s user=%s vhost=%s", sf->sf_host, sf->sf_port, sf->sf_user, RMQ_VHOST);
#endif

  rmq_conn = amqp_new_connection();
  if (rmq_conn == NULL) {
#ifdef DEBUG
    ERROR("amqp_new_connection failed (out of memory?)");
#else
    ERROR("amqp_new_connection failed");
#endif
    rmq_arm_connect_backoff();
    return -1;
  }

  amqp_socket_t *sock = NULL;
  if (rmq_open_tcp_and_login(rmq_conn, sf, &sock, &rmq_channel_open) < 0) {
    close_rmq_connection(rmq_conn, rmq_channel_open);
    rmq_conn = NULL;
    rmq_channel_open = 0;
    rmq_arm_connect_backoff();
    return -1;
  }

  if (!rmq_stored_matches(sf) && rmq_stored_save(sf) < 0) {
#ifdef DEBUG
    ERROR("RMQ: failed to stash broker credentials after connect (strdup)");
#endif
    close_rmq_connection(rmq_conn, rmq_channel_open);
    rmq_conn = NULL;
    rmq_channel_open = 0;
    rmq_arm_connect_backoff();
    return -1;
  }

#ifdef DEBUG
  ERROR("RMQ: TCP + login + channel open OK");
#endif
  rmq_clear_connect_backoff();
  return 0;
}

static int rmq_ensure_queue(struct stats_buffer *sf)
{
  if (rmq_declared_queue != NULL && strcmp(rmq_declared_queue, sf->sf_queue) == 0)
    return 0;

  free(rmq_declared_queue);
  rmq_declared_queue = NULL;

  if (rmq_declare_queue_and_bind_to_exchange(rmq_conn, sf) < 0)
    return -1;

  rmq_declared_queue = strdup(sf->sf_queue);
  if (rmq_declared_queue == NULL)
    return -1;
  return 0;
}

static int rmq_open_tcp_and_login(amqp_connection_state_t conn, struct stats_buffer *sf,
				    amqp_socket_t **socket_out, int *channel_opened_out)
{
  amqp_socket_t *socket = amqp_tcp_socket_new(conn);
  int sock_rc;

  *socket_out = socket;
  if (!socket) {
#ifdef DEBUG
    ERROR("RMQ: amqp_tcp_socket_new failed");
#else
    ERROR("socket failed to initialize");
#endif
    return -1;
  }
  sock_rc = amqp_socket_open(socket, sf->sf_host, atoi(sf->sf_port));
  if (sock_rc != AMQP_STATUS_OK) {
#ifdef DEBUG
    rmq_debug_log_amqp_status("RMQ amqp_socket_open", sock_rc);
#else
    ERROR("socket failed to open");
#endif
    return -1;
  }

  amqp_rpc_reply_t ret = amqp_login(conn, RMQ_VHOST, 0, 131072, 0, AMQP_SASL_METHOD_PLAIN,
				      sf->sf_user, sf->sf_password);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
    rmq_debug_log_rpc_reply("RMQ amqp_login", ret);
#else
    ERROR("amqp login failed");
#endif
    return -1;
  }
  amqp_channel_open(conn, RMQ_CHANNEL);
  ret = amqp_get_rpc_reply(conn);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
    rmq_debug_log_rpc_reply("RMQ amqp_channel_open", ret);
#else
    ERROR("amqp channel open failed");
#endif
    return -1;
  }
  *channel_opened_out = 1;
  return 0;
}

static int rmq_declare_queue_and_bind_to_exchange(amqp_connection_state_t conn, struct stats_buffer *sf)
{
#ifdef DEBUG
  syslog(LOG_INFO, "Attempt declare queue on RMQ server\n");
#endif
  amqp_queue_declare_ok_t *r = amqp_queue_declare(conn, RMQ_CHANNEL, amqp_cstring_bytes(sf->sf_queue),
						  0, 1, 0, 0, amqp_empty_table);
  amqp_rpc_reply_t ret = amqp_get_rpc_reply(conn);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
    rmq_debug_log_rpc_reply("RMQ queue declare", ret);
#else
    syslog(LOG_ERR, "queue declare failed");
#endif
    return -1;
  }

  amqp_bytes_t reply_to_queue = amqp_bytes_malloc_dup(r->queue);
  if (reply_to_queue.bytes == NULL) {
    syslog(LOG_ERR, "Out of memory while copying queue name");
    return -1;
  }

  amqp_queue_bind(conn, RMQ_CHANNEL, reply_to_queue, amqp_cstring_bytes(RMQ_EXCHANGE),
		  amqp_cstring_bytes(sf->sf_queue), amqp_empty_table);
  ret = amqp_get_rpc_reply(conn);
  amqp_bytes_free(reply_to_queue);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
    rmq_debug_log_rpc_reply("RMQ queue bind", ret);
#else
    syslog(LOG_ERR, "queue bind failed");
#endif
    return -1;
  }
  return 0;
}

static int rmq_publish_text_payload(amqp_connection_state_t conn, struct stats_buffer *sf)
{
  amqp_basic_properties_t props;
  props._flags = AMQP_BASIC_CONTENT_TYPE_FLAG | AMQP_BASIC_DELIVERY_MODE_FLAG;
  props.content_type = amqp_cstring_bytes("text/plain");
  props.delivery_mode = 2; /* persistent delivery mode */
  int status = amqp_basic_publish(conn,
				  RMQ_CHANNEL,
				  amqp_cstring_bytes(RMQ_EXCHANGE),
				  amqp_cstring_bytes(sf->sf_queue),
				  0,
				  0,
				  &props,
				  amqp_cstring_bytes(sf->sf_data));
  if (status != AMQP_STATUS_OK) {
#ifdef DEBUG
    rmq_debug_log_amqp_status("RMQ amqp_basic_publish", status);
#else
    ERROR("amqp basic publish failed");
#endif
    return -1;
  }
  return 0;
}

static int send(struct stats_buffer *sf)
{
  if (rmq_ensure_connected(sf) < 0)
    return -1;

  if (rmq_ensure_queue(sf) < 0) {
    rmq_soft_disconnect();
    rmq_arm_connect_backoff();
    return -1;
  }

  if (rmq_publish_text_payload(rmq_conn, sf) < 0) {
    rmq_soft_disconnect();
    rmq_arm_connect_backoff();
    return -1;
  }

  rmq_clear_connect_backoff();
  return 0;
}

int stats_wr_hdr(struct stats_buffer *sf)
{
  unsigned long long uptime = 0;

  stats_buffer_ensure_uts_cached();
  pscanf("/proc/uptime", "%llu", &uptime);

  stats_buffer_append_fmt(sf, "%c%s %s\n", SF_PROPERTY_CHAR, STATS_PROGRAM, STATS_VERSION);
  stats_buffer_append_fmt(sf, "%chostname %s\n", SF_PROPERTY_CHAR, cached_uts.nodename);
  stats_buffer_append_fmt(sf, "%cuname %s %s %s %s\n", SF_PROPERTY_CHAR, cached_uts.sysname,
			  cached_uts.machine, cached_uts.release, cached_uts.version);
  stats_buffer_append_fmt(sf, "%cuptime %llu\n", SF_PROPERTY_CHAR, uptime);
  
  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;

    TRACE("type %s, schema_len %zu\n", type->st_name, type->st_schema.sc_len);
    stats_buffer_append_schema_line_for_type(sf, type);
  }

  return 0;
}

int stats_buffer_open(struct stats_buffer *sf, const char *host, const char *port, const char *queue, const char *user, const char *password)
{
  int rc = 0;
  memset(sf, 0, sizeof(*sf));
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
  if (sf->sf_host == NULL || sf->sf_port == NULL || sf->sf_queue == NULL
      || sf->sf_user == NULL || sf->sf_password == NULL) {
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
  /* TODO Concatenate new mark with old. */
  va_list args;
  va_start(args, fmt);
  free(sf->sf_mark);
  sf->sf_mark = NULL;

  if (vasprintf(&sf->sf_mark, fmt, args) < 0)
    sf->sf_mark = NULL;

  va_end(args);
  return 0;
}

static void stats_buffer_append_mark_lines(struct stats_buffer *sf)
{
  if (sf->sf_mark == NULL)
    return;
  const char *str = sf->sf_mark;
  while (*str != 0) {
    const char *eol = strchrnul(str, '\n');
    stats_buffer_append_fmt(sf, "%c%*s\n", SF_MARK_CHAR, (int)(eol - str), str);
    str = eol;
    if (*str == '\n')
      str++;
  }
}

static int stats_buffer_append_type_row(struct stats_buffer *sf, struct stats_type *type, struct stats *stats)
{
  size_t k;

  for (int attempt = 0; attempt < 8; attempt++) {
    size_t need = strlen(type->st_name) + 1 + strlen(stats->s_dev) + 4;
    for (k = 0; k < type->st_schema.sc_len; k++)
      need += 24;
    if (need < 256)
      need = 256;
    if (need > row_line_cap) {
      char *nr = realloc(row_line_buf, need);
      if (nr == NULL)
	return -1;
      row_line_buf = nr;
      row_line_cap = need;
    }

    char *p = row_line_buf;
    char *end = row_line_buf + row_line_cap;
    int n = snprintf(p, (size_t)(end - p), "%s %s", type->st_name, stats->s_dev);
    if (n < 0)
      return -1;
    if ((size_t)n >= (size_t)(end - p))
      continue;
    p += n;
    int ok = 1;
    for (k = 0; k < type->st_schema.sc_len; k++) {
      n = snprintf(p, (size_t)(end - p), " %llu", stats->s_val[k]);
      if (n < 0)
	return -1;
      if ((size_t)n >= (size_t)(end - p)) {
	ok = 0;
	break;
      }
      p += n;
    }
    if (!ok)
      continue;
    if ((size_t)(end - p) < 2)
      continue;
    *p++ = '\n';
    return stats_buffer_data_append_bytes(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, row_line_buf,
					  (size_t)(p - row_line_buf));
  }
  return -1;
}

static void stats_buffer_append_enabled_type_rows(struct stats_buffer *sf)
{
  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!(type->st_enabled))
      continue;

    size_t j = 0;
    char *dev;
    while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL) {
      struct stats *stats = key_to_stats(dev);

      if (stats_buffer_append_type_row(sf, type, stats) < 0) {
	stats_buffer_append_fmt(sf, "%s %s", type->st_name, stats->s_dev);
	for (size_t k = 0; k < type->st_schema.sc_len; k++)
	  stats_buffer_append_fmt(sf, " %llu", stats->s_val[k]);
	stats_buffer_append_fmt(sf, "\n");
      }
    }
  }
}

int stats_buffer_write(struct stats_buffer *sf)
{
  int rc = 0;

  struct timespec time;

  if (clock_gettime(CLOCK_REALTIME, &time) != 0) {
    fprintf(stderr, "cannot clock_gettime(): %m\n");
    goto out;
  }
  stats_buffer_ensure_uts_cached();
  stats_buffer_append_fmt(sf, "\n%f %s %s\n", time.tv_sec + 1e-9 * time.tv_nsec, jobid,
			  cached_uts.nodename);

  stats_buffer_append_mark_lines(sf);
  stats_buffer_append_enabled_type_rows(sf);
  rc = send(sf);

  /* For debugging */
  /*if ((double)rand() / (double)RAND_MAX < 0.9)
    rc = -1;
  else
    rc = 0;*/
 out:
  return rc;
}

// A modified send function with a controllable failure rate (for debugging)
int stats_buffer_resend(struct stats_buffer *sf)
{
  /* For debugging */
  /*if ((double)rand() / (double)RAND_MAX < 0)
    return -1;
  else
    return 0;*/
  return send(sf);
}

int ring_buffer_insert(
  struct stats_buffer *sf, 
  struct sf_ring_buffer *w, 
  int max_buffer_size, 
  int allow_ring_buffer_overwrite)
{ 
  int rc = 0;
  struct sf_queue *q_new;
  
  /* Case 1: Empty buffer */
  if (w->q_count == 0) {
    q_new = (struct sf_queue *) calloc(1, sizeof(struct sf_queue));
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
    w->q->forward->sf = sf;
    w->q = w->q->forward;
    w->q_first = w->q->forward;
    w->d_count += 1;
    goto out;
  }
  
  /* Case 3: Otherwise */
  q_new = (struct sf_queue *) calloc(1, sizeof(struct sf_queue));
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

void ring_buffer_resend(struct sf_ring_buffer *w)
{
  struct sf_queue * sf = w->q_first;
  struct sf_queue * sf_del;
  int reset_first;
  do {
    reset_first = 0;
    /* Resend stats_buffer */
    w->status = stats_buffer_resend(sf->sf);
    /* Move to the next if failed */
    if (w->status == -1)  {
      sf = sf->forward;
      continue;
    }
    else
      w->r_count++;
    /* Case 1: Remove the last stats in buffer */
    if (w->q_count == 1) {
      stats_buffer_close(sf->sf);
      sf_del = sf;
      remque(sf);
      free(sf_del);
      w->q_count -= 1;
      continue;
    }
    /* Case 2: Remove the head stats in buffer */
    if (sf == w->q_first) {
      w->q_first = sf->forward;
      reset_first = 1;
    } /* Case 3: Remove the lastest stats in buffer */
    else if (sf == w->q)  {
      w->q = sf->backward;
    }
    sf = sf->forward;
    stats_buffer_close((sf->backward)->sf);
    sf_del = sf->backward;
    remque(sf->backward);
    free(sf_del);
    w->q_count -= 1;
  } while ((sf != w->q_first || reset_first == 1) && w->q_count > 0);
}

int stats_buffer_write_file(struct stats_buffer *sf, char *path)
{
  int rc = 0;
  FILE *sf_file = file_fopen_append(path);
  if (sf_file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
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

int ring_buffer_load_file(
  FILE *sf_file, 
  struct sf_ring_buffer *w, 
  const char *host, 
  const char *port, 
  const char *queue,
  const char *user,
  const char *password,
  int max_buffer_size, 
  int allow_ring_buffer_overwrite)
{
  int n_stats = 0;
  int stats_start = 0;
  int rc = 0;
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  struct stats_buffer *sf;
  sf = (struct stats_buffer *) malloc(sizeof(*sf));
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
  while (getline(&line_buf, &line_buf_size, sf_file) != -1)  {
    if (line_buf[0] == '\n' && stats_start == 0)
        continue;
    if (line_buf[0] != '\n')  {
      stats_buffer_append_fmt(sf, "%s", line_buf);
      if (stats_start == 0)
          stats_start = 1;
    }
    else {
      n_stats++;
      rc = ring_buffer_insert(sf, w, -1, allow_ring_buffer_overwrite);
      sf = (struct stats_buffer *) malloc(sizeof(struct stats_buffer));
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
