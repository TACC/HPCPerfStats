#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <sys/utsname.h>
#include <syslog.h>
#include <search.h>
#include <amqp.h>
#include <time.h>

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

#define sf_printf(sf, fmt, args...) do {			\
    char *tmp_string = sf->sf_data;				\
    asprintf(&(sf->sf_data), "%s"fmt, sf->sf_data, ##args);	\
    free(tmp_string);						\
  } while(0)

static int send(struct stats_buffer *sf)
{
  int status = -1;
  char const *exchange;
  amqp_socket_t *socket = NULL;
  amqp_connection_state_t conn;

  static int queue_declared = 0;

  exchange = "amq.direct";
  conn = amqp_new_connection();
  socket = amqp_tcp_socket_new(conn);

  if (!socket) {
    ERROR("socket failed to initialize");
    return -1;	
  }
  status = amqp_socket_open(socket, sf->sf_host, atoi(sf->sf_port));
  if (status) {
    ERROR("socket failed to open");
    return -1;	  
  }

  amqp_login(conn, "/", 0, 131072, 0, AMQP_SASL_METHOD_PLAIN, 
	     "guest", "guest");
  amqp_channel_open(conn, 1);
  amqp_get_rpc_reply(conn);

  if (!queue_declared) {
    syslog(LOG_INFO, "Attempt declare queue on RMQ server\n");
    amqp_queue_declare_ok_t *r = amqp_queue_declare(conn, 1, amqp_cstring_bytes(sf->sf_queue), 
						    0, 1, 0, 0, amqp_empty_table);
    amqp_rpc_reply_t ret = amqp_get_rpc_reply(conn);
    if (ret.reply_type != AMQP_RESPONSE_NORMAL) {
      syslog(LOG_ERR, "queue declare failed");
      return -1;
    }
    else {
      amqp_bytes_t reply_to_queue;
      reply_to_queue = amqp_bytes_malloc_dup(r->queue);
      if (reply_to_queue.bytes == NULL) {
        syslog(LOG_ERR, "Out of memory while copying queue name");
        return -1;
      }
      
      amqp_queue_bind(conn, 1, reply_to_queue, amqp_cstring_bytes(exchange), 
		      amqp_cstring_bytes(sf->sf_queue), amqp_empty_table);
      amqp_get_rpc_reply(conn);
      queue_declared = 1;
      amqp_bytes_free(reply_to_queue);
    }
  }

  {
    amqp_basic_properties_t props;
    props._flags = AMQP_BASIC_CONTENT_TYPE_FLAG | AMQP_BASIC_DELIVERY_MODE_FLAG;
    props.content_type = amqp_cstring_bytes("text/plain");
    props.delivery_mode = 2; /* persistent delivery mode */
    amqp_basic_publish(conn,
		       1,
		       amqp_cstring_bytes(exchange),
		       amqp_cstring_bytes(sf->sf_queue),
		       0,
		       0,
		       &props,
		       amqp_cstring_bytes(sf->sf_data));
  }

  amqp_destroy_connection(conn); 

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

    /* Write schema. */
    sf_printf(sf, "%c%s", SF_SCHEMA_CHAR, type->st_name);

    /* MOVEME */
    size_t j;
    for (j = 0; j < type->st_schema.sc_len; j++) {
      struct schema_entry *se = type->st_schema.sc_ent[j];
      sf_printf(sf, " %s", se->se_key);
      if (se->se_type == SE_CONTROL)
        sf_printf(sf, ",C");
      if (se->se_type == SE_EVENT)
        sf_printf(sf, ",E");
      if (se->se_unit != NULL)
        sf_printf(sf, ",U=%s", se->se_unit);
      if (se->se_width != 0)
        sf_printf(sf, ",W=%u", se->se_width);
    }
    sf_printf(sf, "\n");
  }

  return 0;
}

int stats_buffer_open(struct stats_buffer *sf, const char *host, const char *port, const char *queue)
{
  int rc = 0;
  memset(sf, 0, sizeof(*sf));
  sf->sf_data=strdup("");
  sf->sf_host=strdup(host);
  sf->sf_port=strdup(port);
  sf->sf_queue=strdup(queue);

  return rc;
}

int stats_buffer_close(struct stats_buffer *sf)
{
  int rc = 0;
  
  free(sf->sf_data);
  free(sf->sf_host);
  free(sf->sf_port);
  free(sf->sf_queue);
  free(sf->sf_mark);
  memset(sf, 0, sizeof(*sf));
  return rc;
}

int stats_buffer_mark(struct stats_buffer *sf, const char *fmt, ...)
{
  /* TODO Concatenate new mark with old. */
  va_list args;
  va_start(args, fmt);

  if (vasprintf(&sf->sf_mark, fmt, args) < 0)
    sf->sf_mark = NULL;

  va_end(args);
  return 0;
}

int stats_buffer_write(struct stats_buffer *sf)
{
  int rc = 0;

  struct utsname uts_buf;
  uname(&uts_buf);

  struct timespec time;

  // Get  time
  if (clock_gettime(CLOCK_REALTIME, &time) != 0) {
    fprintf(stderr, "cannot clock_gettime(): %m\n");
    goto out;
  }
  sf_printf(sf, "\n%f %s %s\n", time.tv_sec + 1e-9*time.tv_nsec, jobid, uts_buf.nodename);

  /* Write mark. */
  if (sf->sf_mark != NULL) {
    const char *str = sf->sf_mark;
    while (*str != 0) {
      const char *eol = strchrnul(str, '\n');
      sf_printf(sf, "%c%*s\n", SF_MARK_CHAR, (int) (eol - str), str);
      str = eol;
      if (*str == '\n')
        str++;
    }
  }

  /* Write stats. */
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
      size_t k;
      for (k = 0; k < type->st_schema.sc_len; k++) {
	      sf_printf(sf, " %llu", stats->s_val[k]);
	    }
      sf_printf(sf, "\n");
    }
  }
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
    q_new = calloc(1, sizeof(struct sf_queue));
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
  q_new = calloc(1, sizeof(struct sf_queue));
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
  if (fclose(sf_file) < 0) {
    ERROR("error closing `%s': %m\n", path);
    rc = -1;
  }
  out:
    return rc;
}

int ring_buffer_load_file(
  FILE *sf_file, 
  struct sf_ring_buffer *w, 
  const char *host, 
  const char *port, 
  const char *queue,
  int max_buffer_size, 
  int allow_ring_buffer_overwrite)
{
  int n_stats = 0;
  int stats_start = 0;
  int rc = 0;
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  struct stats_buffer *sf;
  sf = malloc(sizeof(*sf));
  if (stats_buffer_open(sf, host, port, queue) < 0) {
    TRACE("Failed opening data buffer : %m\n");
    rc = -1;
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
      sf = malloc(sizeof(struct stats_buffer));
      if (stats_buffer_open(sf, host, port, queue) < 0 || rc < 0) {
        TRACE("Failed inserting data to buffer : %m\n");
        rc = -1;
        goto out;
      }
    }
  }
  rc = ring_buffer_insert(sf, w, -1, allow_ring_buffer_overwrite);
  n_stats++;
  w->l_count += n_stats;
  TRACE("Loaded %d stats from dumpfile\n", n_stats);

  out:
    return rc;
}
