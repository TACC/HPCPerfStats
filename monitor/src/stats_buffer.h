#ifndef _STATS_BUFFER_H_
#define _STATS_BUFFER_H_
#include <stdio.h>

#include "stats_text_format.h"

struct stats_buffer {
  char *sf_mark;
  char *sf_data;
  size_t sf_data_len;
  size_t sf_data_cap;
  char *sf_host;
  char *sf_queue;
  char *sf_port;
  char *sf_user;
  char *sf_password;
  unsigned int sf_empty : 1;
};

struct sf_queue {
  struct sf_queue *forward;
  struct sf_queue *backward;
  struct stats_buffer *sf;
};

struct sf_ring_buffer {
  struct sf_queue *q;       // the lastest stats in buffer
  struct sf_queue *q_first; // the head stats in buffer
  int status;               // send_stats_buffer status
  int q_count;              // # of stats in buffer
  int b_count;              // # of stats processed (accumulated)
  int d_count;              // # of stats deleted (accumulated)
  int s_count;              // # of successful sent stats (accumulated)
  int r_count;              // # of successful resent stats (accumulated)
  int f_count;              // # of stats dumped in file (accumulated)
  int l_count;              // # of stats loaded from file (accumulated)
};

int stats_buffer_open(struct stats_buffer *sf, const char *host, const char *port,
                      const char *queue, const char *user, const char *password);
int stats_buffer_close(struct stats_buffer *sf);
int stats_buffer_mark(struct stats_buffer *sf, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
int stats_buffer_write(struct stats_buffer *sf);
int stats_buffer_collect(struct stats_buffer *sf);
enum stats_row_tier stats_buffer_payload_row_tier(const struct stats_buffer *sf);
int stats_wr_hdr(struct stats_buffer *sf);
int stats_buffer_resend(struct stats_buffer *sf);
int stats_buffer_write_file(struct stats_buffer *sf, char *path);

void ring_buffer_resend(struct sf_ring_buffer *w);
void ring_buffer_resend_limited(struct sf_ring_buffer *w, int max_batches, long max_runtime_us,
                                int *processed_entries);

int ring_buffer_insert(struct stats_buffer *sf, struct sf_ring_buffer *w, int max_buffer_size,
                       int allow_ring_buffer_overwrite);

int ring_buffer_load_file(FILE *sf_file, struct sf_ring_buffer *w, const char *host,
                          const char *port, const char *queue, const char *user,
                          const char *password, int max_buffer_size,
                          int allow_ring_buffer_overwrite);

void stats_buffer_rmq_shutdown(void);
void stats_buffer_runtime_caches_reset(void);
/* Non-blocking AMQP I/O so rabbitmq-c can emit heartbeats between publishes (see amqp_login heartbeat note). */
void stats_buffer_rmq_service_io(void);

#ifdef STATS_BUFFER_TEST_SEND_HOOK
/* Unit tests provide this to exercise ring_buffer_resend without a live broker. */
int stats_buffer_test_send_hook(struct stats_buffer *sf);
#endif

#endif
