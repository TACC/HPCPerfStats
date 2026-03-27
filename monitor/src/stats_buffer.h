#ifndef _STATS_BUFFER_H_
#define _STATS_BUFFER_H_
#include <stdio.h>

struct stats_buffer {
  char *sf_mark;
  char *sf_data;
  char *sf_host;
  char *sf_queue;
  char *sf_port;
  char *sf_user;
  char *sf_password;
  unsigned int sf_empty:1;
};

struct sf_queue {
  struct sf_queue *forward;
  struct sf_queue *backward;
  struct stats_buffer *sf;
};

struct sf_ring_buffer {
  struct sf_queue *q; // the lastest stats in buffer
  struct sf_queue *q_first; // the head stats in buffer
  int status;  // send_stats_buffer status
  int q_count; // # of stats in buffer
  int b_count; // # of stats processed (accumulated)
  int d_count; // # of stats deleted (accumulated)
  int s_count; // # of successful sent stats (accumulated)
  int r_count; // # of successful resent stats (accumulated)
  int f_count; // # of stats dumped in file (accumulated)
  int l_count; // # of stats loaded from file (accumulated)
};

int stats_buffer_open(struct stats_buffer *sf, const char *host, const char *port, const char *queue, const char *user, const char *password);
int stats_buffer_close(struct stats_buffer *sf);
int stats_buffer_mark(struct stats_buffer *sf, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
int stats_buffer_write(struct stats_buffer *sf);
int stats_wr_hdr(struct stats_buffer *sf);
int stats_buffer_resend(struct stats_buffer *sf);
int stats_buffer_write_file(struct stats_buffer *sf, char *path);

void ring_buffer_resend(struct sf_ring_buffer *w);

int ring_buffer_insert(
  struct stats_buffer *sf, 
  struct sf_ring_buffer *w, 
  int max_buffer_size, 
  int allow_ring_buffer_overwrite);

int ring_buffer_load_file(
  FILE *sf_file, 
  struct sf_ring_buffer *w, 
  const char *host, 
  const char *port, 
  const char *queue,
  const char *user,
  const char *password,
  int max_buffer_size, 
  int allow_ring_buffer_overwrite);

#endif
