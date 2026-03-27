#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <sys/utsname.h>
#include <syslog.h>
#include <search.h>
#include <time.h>
#include <rabbitmq-c/amqp.h>
#include <rabbitmq-c/tcp_socket.h>

#include "stats.h"
#include "stats_buffer.h"
#include "schema.h"
#include "trace.h"
#include "pscanf.h"
#include "string1.h"

#define SF_SCHEMA_CHAR '!'
#define SF_DEVICES_CHAR '@'
#define SF_COMMENT_CHAR '#'
#define SF_PROPERTY_CHAR '$'
#define SF_MARK_CHAR '%'
#define RMQ_EXCHANGE "amq.direct"
#define RMQ_VHOST "/"
#define RMQ_CHANNEL 1

#define sf_printf(sf, fmt, args...) do {			\
    char *tmp_string = sf->sf_data;				\
    asprintf(&(sf->sf_data), "%s" fmt, sf->sf_data, ##args);	\
    free(tmp_string);						\
  } while(0)

static void stats_buffer_append_schema_entry_suffix(struct stats_buffer *sf, struct schema_entry *se)
{
  if (se->se_type == SE_CONTROL)
    sf_printf(sf, ",C");
  if (se->se_type == SE_EVENT)
    sf_printf(sf, ",E");
  if (se->se_unit != NULL)
    sf_printf(sf, ",U=%s", se->se_unit);
  if (se->se_width != 0)
    sf_printf(sf, ",W=%u", se->se_width);
}

static void stats_buffer_append_schema_line_for_type(struct stats_buffer *sf, struct stats_type *type)
{
  sf_printf(sf, "%c%s", SF_SCHEMA_CHAR, type->st_name);
  for (size_t j = 0; j < type->st_schema.sc_len; j++) {
    struct schema_entry *se = type->st_schema.sc_ent[j];
    sf_printf(sf, " %s", se->se_key);
    stats_buffer_append_schema_entry_suffix(sf, se);
  }
  sf_printf(sf, "\n");
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

static int rmq_open_tcp_and_login(amqp_connection_state_t conn, struct stats_buffer *sf,
				    amqp_socket_t **socket_out, int *channel_opened_out)
{
  amqp_socket_t *socket = amqp_tcp_socket_new(conn);
  *socket_out = socket;
  if (!socket) {
    ERROR("socket failed to initialize");
    return -1;
  }
  if (amqp_socket_open(socket, sf->sf_host, atoi(sf->sf_port))) {
    ERROR("socket failed to open");
    return -1;
  }

  amqp_rpc_reply_t ret = amqp_login(conn, RMQ_VHOST, 0, 131072, 0, AMQP_SASL_METHOD_PLAIN,
				      sf->sf_user, sf->sf_password);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
    ERROR("amqp login failed");
    return -1;
  }
  amqp_channel_open(conn, RMQ_CHANNEL);
  ret = amqp_get_rpc_reply(conn);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
    ERROR("amqp channel open failed");
    return -1;
  }
  *channel_opened_out = 1;
  return 0;
}

static int rmq_declare_queue_and_bind_to_exchange(amqp_connection_state_t conn, struct stats_buffer *sf)
{
  syslog(LOG_INFO, "Attempt declare queue on RMQ server\n");
  amqp_queue_declare_ok_t *r = amqp_queue_declare(conn, RMQ_CHANNEL, amqp_cstring_bytes(sf->sf_queue),
						  0, 1, 0, 0, amqp_empty_table);
  amqp_rpc_reply_t ret = amqp_get_rpc_reply(conn);
  if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
    syslog(LOG_ERR, "queue declare failed");
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
    syslog(LOG_ERR, "queue bind failed");
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
    ERROR("amqp basic publish failed");
    return -1;
  }
  return 0;
}

static int send(struct stats_buffer *sf)
{
  amqp_socket_t *socket = NULL;
  amqp_connection_state_t conn = amqp_new_connection();
  int channel_opened = 0;

  if (conn == NULL) {
    ERROR("amqp_new_connection failed");
    return -1;
  }

  if (rmq_open_tcp_and_login(conn, sf, &socket, &channel_opened) < 0) {
    close_rmq_connection(conn, channel_opened);
    return -1;
  }

  if (rmq_declare_queue_and_bind_to_exchange(conn, sf) < 0) {
    close_rmq_connection(conn, channel_opened);
    return -1;
  }

  if (rmq_publish_text_payload(conn, sf) < 0) {
    close_rmq_connection(conn, channel_opened);
    return -1;
  }

  close_rmq_connection(conn, channel_opened);
  return 0;
}

int stats_wr_hdr(struct stats_buffer *sf)
{
  struct utsname uts_buf;
  unsigned long long uptime = 0;
  
  uname(&uts_buf);
  pscanf("/proc/uptime", "%llu", &uptime);
  
  sf_printf(sf, "%c%s %s\n", SF_PROPERTY_CHAR, STATS_PROGRAM, STATS_VERSION);
  sf_printf(sf, "%chostname %s\n", SF_PROPERTY_CHAR, uts_buf.nodename);
  sf_printf(sf, "%cuname %s %s %s %s\n", SF_PROPERTY_CHAR, uts_buf.sysname,
            uts_buf.machine, uts_buf.release, uts_buf.version);
  sf_printf(sf, "%cuptime %llu\n", SF_PROPERTY_CHAR, uptime);
  
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
  sf->sf_data=strdup("");
  sf->sf_host=strdup(host);
  sf->sf_port=strdup(port);
  sf->sf_queue=strdup(queue);
  sf->sf_user=strdup(user);
  sf->sf_password=strdup(password);

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
    sf_printf(sf, "%c%*s\n", SF_MARK_CHAR, (int) (eol - str), str);
    str = eol;
    if (*str == '\n')
      str++;
  }
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

      sf_printf(sf, "%s %s", type->st_name, stats->s_dev);
      for (size_t k = 0; k < type->st_schema.sc_len; k++)
        sf_printf(sf, " %llu", stats->s_val[k]);
      sf_printf(sf, "\n");
    }
  }
}

int stats_buffer_write(struct stats_buffer *sf)
{
  int rc = 0;
  struct utsname uts_buf;
  uname(&uts_buf);

  struct timespec time;

  if (clock_gettime(CLOCK_REALTIME, &time) != 0) {
    fprintf(stderr, "cannot clock_gettime(): %m\n");
    goto out;
  }
  sf_printf(sf, "\n%f %s %s\n", time.tv_sec + 1e-9 * time.tv_nsec, jobid, uts_buf.nodename);

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
  FILE *sf_file = fopen(path, "a+");
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
      sf_printf(sf, "%s", line_buf);
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
