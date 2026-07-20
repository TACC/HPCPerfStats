/* RabbitMQ connection lifecycle and publish for stats_buffer (daemon). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <rabbitmq-c/amqp.h>
#include <rabbitmq-c/framing.h>
#include <rabbitmq-c/tcp_socket.h>

#include "stats_buffer.h"
#include "stats_buffer_rmq_internal.h"
#include "trace.h"
#include "monitor_log.h"

/* RMQ send interval from monitor_daemon.c; reconnect backoff uses min(send_freq, cap) with a floor. */
extern double send_freq;

#define RMQ_EXCHANGE "amq.direct"
#define RMQ_VHOST "/"
#define RMQ_CHANNEL 1

/* Bounded broker I/O (libev thread); generous wall-clock limits vs kernel defaults. */
#define RMQ_TCP_CONNECT_TIMEOUT_SEC 25
#define RMQ_HANDSHAKE_TIMEOUT_SEC 15
#define RMQ_RPC_TIMEOUT_SEC 30
#define RMQ_SOCK_IO_TIMEOUT_SEC 30
#define RMQ_AMQP_HEARTBEAT_MIN_SEC 30

/* Reconnect pacing: sample/send cadence still uses send_freq elsewhere; TCP retry delay is capped. */
#define RMQ_RECONNECT_BACKOFF_CAP_SEC 30
#define RMQ_RECONNECT_BACKOFF_MIN_SEC 2

static int rmq_open_tcp_and_login(amqp_connection_state_t conn, struct stats_buffer *sf,
                                  amqp_socket_t **socket_out, int *channel_opened_out);
static int rmq_declare_queue_and_bind_to_exchange(amqp_connection_state_t conn,
                                                  struct stats_buffer *sf);

#ifdef DEBUG
/* Decode rabbitmq-c failures for DEBUG builds (ERROR -> stdout when RABBITMQ+DEBUG). */
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
 * When the broker is down, TCP reconnect attempts are rate-limited with a delay of
 * max(RMQ_RECONNECT_BACKOFF_MIN_SEC, min(send_freq, RMQ_RECONNECT_BACKOFF_CAP_SEC)) seconds so ring-buffer
 * resend loops in one libev tick do not storm the network, while large send_freq does not postpone retries
 * for many minutes. Stats sampling and payload scheduling still follow send_freq in the daemon. */
static amqp_connection_state_t rmq_conn;
static int rmq_channel_open;
static struct timespec rmq_backoff_until;
static int rmq_backoff_until_valid;
static unsigned long rmq_connect_failures;
static unsigned long rmq_queue_failures;
static unsigned long rmq_publish_failures;

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
  if (now.tv_sec < rmq_backoff_until.tv_sec ||
      (now.tv_sec == rmq_backoff_until.tv_sec && now.tv_nsec < rmq_backoff_until.tv_nsec))
    return 1;
  rmq_backoff_until_valid = 0;
  return 0;
}

static void rmq_arm_connect_backoff(void)
{
  struct timespec now;
  double f = send_freq;
  double delay;

  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
    return;
  if (f <= 0.0)
    f = 1.0;
  delay = f;
  if (delay > (double)RMQ_RECONNECT_BACKOFF_CAP_SEC)
    delay = (double)RMQ_RECONNECT_BACKOFF_CAP_SEC;
  if (delay < (double)RMQ_RECONNECT_BACKOFF_MIN_SEC)
    delay = (double)RMQ_RECONNECT_BACKOFF_MIN_SEC;
  {
    time_t add_sec = (time_t)delay;
    double frac = delay - (double)add_sec;

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

static void rmq_timeval_seconds(struct timeval *tv, long sec)
{
  tv->tv_sec = sec;
  tv->tv_usec = 0;
}

static int rmq_apply_sock_timeouts(amqp_socket_t *socket)
{
  struct timeval tv;
  int fd;

  if (socket == NULL)
    return -1;
  fd = amqp_socket_get_sockfd(socket);
  if (fd < 0)
    return -1;
  rmq_timeval_seconds(&tv, RMQ_SOCK_IO_TIMEOUT_SEC);
  if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, (socklen_t)sizeof(tv)) != 0)
    return -1;
  if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, (socklen_t)sizeof(tv)) != 0)
    return -1;
  return 0;
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
  return strcmp(rmq_stored_host, sf->sf_host) == 0 && strcmp(rmq_stored_port, sf->sf_port) == 0 &&
         strcmp(rmq_stored_user, sf->sf_user) == 0 && strcmp(rmq_stored_pass, sf->sf_password) == 0;
}

static int rmq_stored_save(struct stats_buffer *sf)
{
  rmq_stored_free();
  rmq_stored_host = strdup(sf->sf_host);
  rmq_stored_port = strdup(sf->sf_port);
  rmq_stored_user = strdup(sf->sf_user);
  rmq_stored_pass = strdup(sf->sf_password);
  if (rmq_stored_host == NULL || rmq_stored_port == NULL || rmq_stored_user == NULL ||
      rmq_stored_pass == NULL) {
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

static int rmq_ensure_connected(struct stats_buffer *sf)
{
  if (rmq_conn != NULL && rmq_stored_matches(sf) && rmq_channel_open)
    return 0;

  if (rmq_connect_backoff_active()) {
#ifdef DEBUG
    ERROR("RMQ: connect backoff active, skipping connect attempt (capped min(send_freq,%ds), floor "
          "%ds)",
          RMQ_RECONNECT_BACKOFF_CAP_SEC, RMQ_RECONNECT_BACKOFF_MIN_SEC);
#endif
    return -1;
  }

  rmq_soft_disconnect();

  if (!rmq_stored_matches(sf))
    rmq_stored_free();

#ifdef DEBUG
  ERROR("RMQ: connecting to %s:%s user=%s vhost=%s", sf->sf_host, sf->sf_port, sf->sf_user,
        RMQ_VHOST);
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
  struct timeval connect_tv;
  struct timeval hs_tv;
  struct timeval rpc_tv;
  int sock_rc;
  int heartbeat_sec = RMQ_AMQP_HEARTBEAT_MIN_SEC;

  *channel_opened_out = 0;
  *socket_out = socket;
  if (!socket) {
#ifdef DEBUG
    ERROR("RMQ: amqp_tcp_socket_new failed");
#else
    ERROR("socket failed to initialize");
#endif
    return -1;
  }

#ifdef HAVE_AMQP_SET_HANDSHAKE_TIMEOUT
  rmq_timeval_seconds(&hs_tv, RMQ_HANDSHAKE_TIMEOUT_SEC);
  sock_rc = amqp_set_handshake_timeout(conn, &hs_tv);
  if (sock_rc != AMQP_STATUS_OK) {
#ifdef DEBUG
    rmq_debug_log_amqp_status("RMQ amqp_set_handshake_timeout", sock_rc);
#else
    ERROR("amqp handshake timeout setup failed");
#endif
    return -1;
  }
#endif

  rmq_timeval_seconds(&connect_tv, RMQ_TCP_CONNECT_TIMEOUT_SEC);
  sock_rc = amqp_socket_open_noblock(socket, sf->sf_host, atoi(sf->sf_port), &connect_tv);
  if (sock_rc != AMQP_STATUS_OK) {
#ifdef DEBUG
    rmq_debug_log_amqp_status("RMQ amqp_socket_open_noblock", sock_rc);
#else
    ERROR("socket failed to open");
#endif
    return -1;
  }

  if (rmq_apply_sock_timeouts(socket) < 0) {
#ifdef DEBUG
    ERROR("RMQ: setsockopt SO_RCVTIMEO/SO_SNDTIMEO failed errno=%d (%s)", errno, strerror(errno));
#else
    ERROR("socket timeout setup failed");
#endif
    return -1;
  }

  /* Keep negotiated heartbeat above idle send cadence to avoid broker-side heartbeat misses. */
  if (send_freq > 0.0) {
    double proposed = send_freq * 2.0;
    if (proposed > (double)heartbeat_sec)
      heartbeat_sec = (int)(proposed + 0.999999);
  }

  {
    amqp_rpc_reply_t ret = amqp_login(conn, RMQ_VHOST, 0, 131072, heartbeat_sec,
                                      AMQP_SASL_METHOD_PLAIN, sf->sf_user, sf->sf_password);

    if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
      rmq_debug_log_rpc_reply("RMQ amqp_login", ret);
#else
      ERROR("amqp login failed");
#endif
      return -1;
    }
  }

#ifdef HAVE_AMQP_SET_RPC_TIMEOUT
  rmq_timeval_seconds(&rpc_tv, RMQ_RPC_TIMEOUT_SEC);
  sock_rc = amqp_set_rpc_timeout(conn, &rpc_tv);
  if (sock_rc != AMQP_STATUS_OK) {
#ifdef DEBUG
    rmq_debug_log_amqp_status("RMQ amqp_set_rpc_timeout", sock_rc);
#else
    ERROR("amqp rpc timeout setup failed");
#endif
    return -1;
  }
#endif

  {
    amqp_rpc_reply_t ret;

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
  }
  *channel_opened_out = 1;
  return 0;
}

static int rmq_declare_queue_and_bind_to_exchange(amqp_connection_state_t conn,
                                                  struct stats_buffer *sf)
{
#ifdef DEBUG
  ERROR("Attempt declare queue on RMQ server\n");
#endif
  amqp_queue_declare_ok_t *r = amqp_queue_declare(
      conn, RMQ_CHANNEL, amqp_cstring_bytes(sf->sf_queue), 0, 1, 0, 0, amqp_empty_table);
  amqp_rpc_reply_t ret = amqp_get_rpc_reply(conn);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
#ifdef DEBUG
    rmq_debug_log_rpc_reply("RMQ queue declare", ret);
#else
    monitor_log_error("queue declare failed\n");
#endif
    return -1;
  }

  amqp_bytes_t reply_to_queue = amqp_bytes_malloc_dup(r->queue);
  if (reply_to_queue.bytes == NULL) {
    ERROR("Out of memory while copying queue name\n");
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
    monitor_log_error("queue bind failed\n");
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
  int status = amqp_basic_publish(conn, RMQ_CHANNEL, amqp_cstring_bytes(RMQ_EXCHANGE),
                                  amqp_cstring_bytes(sf->sf_queue), 0, 0, &props,
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

#ifdef STATS_BUFFER_TEST_SEND_HOOK
int stats_buffer_send_payload(struct stats_buffer *sf)
{
  return stats_buffer_test_send_hook(sf);
}
#else
int stats_buffer_send_payload(struct stats_buffer *sf)
{
  if (rmq_ensure_connected(sf) < 0) {
    rmq_connect_failures++;
    ERROR("RMQ connect/attach failed (count=%lu)\n", rmq_connect_failures);
    return -1;
  }

  if (rmq_ensure_queue(sf) < 0) {
    rmq_queue_failures++;
    ERROR("RMQ queue declare/bind failed (count=%lu)\n", rmq_queue_failures);
    rmq_soft_disconnect();
    rmq_arm_connect_backoff();
    return -1;
  }

  if (rmq_publish_text_payload(rmq_conn, sf) < 0) {
    rmq_publish_failures++;
    ERROR("RMQ publish failed (count=%lu)\n", rmq_publish_failures);
    rmq_soft_disconnect();
    rmq_arm_connect_backoff();
    return -1;
  }

  rmq_clear_connect_backoff();
  return 0;
}
#endif
void stats_buffer_rmq_shutdown(void)
{
  rmq_soft_disconnect();
  rmq_stored_free();
  rmq_clear_connect_backoff();
}

void stats_buffer_rmq_service_io(void)
{
  amqp_frame_t frame;
  struct timeval tv0;
  int st;
  unsigned int n;

  if (rmq_conn == NULL || !rmq_channel_open)
    return;

  tv0.tv_sec = 0;
  tv0.tv_usec = 0;

  /* Heartbeats are driven inside wait_frame_inner(); publish-only workloads need periodic calls. */
  for (n = 0; n < 64; n++) {
    st = amqp_simple_wait_frame_noblock(rmq_conn, &frame, &tv0);
    if (st == AMQP_STATUS_TIMEOUT)
      break;
    if (st != AMQP_STATUS_OK) {
#ifdef DEBUG
      rmq_debug_log_amqp_status("RMQ service_io", st);
#endif
      rmq_soft_disconnect();
      rmq_arm_connect_backoff();
      return;
    }
  }
}
