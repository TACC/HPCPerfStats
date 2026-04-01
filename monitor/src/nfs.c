#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>
#include <unistd.h>
#include "collect.h"
#include "fileio.h"
#include "stats.h"
#include "string1.h"
#include "trace.h"

/* Copyright 2011 Charng-Da Lu <charngda@ccr.buffalo.edu>
 * Revised 2011 John L. Hammond <jhammond@tacc.utexas.edu> */

/* Event counters.  See fs/nfs/iostat.h and nfs_show_stats() in
 * fs/nfs/super.c. */

#define EVENT_KEYS \
  X(delay,             "E", "")

static void nfs_collect_mnt_events(struct stats *stats, char *str)
{
#define X(k,r...) #k
  str_collect_key_list(str, stats, EVENT_KEYS, NULL);
#undef X
}

/* "Bytes" counters, also from nfs_show_stats(). */
/* TODO Add U=P for page sized units. */

#define BYTE_KEYS \
  X(normal_read,  "E,U=B", ""), \
  X(normal_write, "E,U=B", ""), \
  X(direct_read,  "E,U=B", ""), \
  X(direct_write, "E,U=B", ""), \
  X(server_read,  "E,U=B", ""), \
  X(server_write, "E,U=B", "")

static void nfs_collect_mnt_bytes(struct stats *stats, char *str)
{
#define X(k,r...) #k
  str_collect_key_list(str, stats, BYTE_KEYS, NULL);
#undef X
}

/* Export (xprt) counters.  See xprt_rdma_print_stats() in
 * net/sunrpc/xprtrdma/transport.c, along with xs_tcp_print_stats()
 * and xs_udp_print_stats() from net/sunrpc/xprtsock.c, and
 * include/linux/sunrpc/xprt.h.
 *
 * Divide xprt_req_u / xprt_sends to get averge number of requests in
 * flight, see xprt_transmit().  Similarly for xprt_bklog_u to get
 * average backlog queue length. */

#define XPRT_KEYS \
  X(xprt_bad_xids, "E", ""), \
  X(xprt_req_u,    "E", "accumulated sum of requests in flight"), \
  X(xprt_bklog_u,  "E", "backlog queue utilization")

static void nfs_collect_mnt_xprt(struct stats *stats, char *str)
{
  char *sock_type = wsep(&str);
  if (sock_type == NULL || str == NULL)
    return;

  /* For UDP, skip port and bind_count.  For TCP, skip those,
   * connect_count, connect_time, and idle_time.  RDMA has all of the
   * TCP counters followed by 10 of its own. */

  int i, nr_to_skip = (strcmp(sock_type, "udp") == 0) ? 2 : 5;

  for (i = 0; i < nr_to_skip; i++)
    wsep(&str);

  if (str == NULL)
    return;

#define X(k,r...) #k
  str_collect_key_list(str, stats, XPRT_KEYS, NULL);
#undef X
}

/* Per-op counters.  See struct rpc_iostats in
 * include/linux/sunrpc/metrics.h and rpc_print_iostats() in
 * net/sunrpc/stats.c. */

#define _OP_DIAG_KEYS(o) \
  X(o##_ops,        "E",      "count of "#o" RPC ops"), \
  X(o##_timeouts,   "E",      "count of "#o" major timeouts"), \
  X(o##_queue,      "E,U=ms", "time "#o" RPC queued for send"), \
  X(o##_rtt,        "E,U=ms", "RTT for "#o" RPC")

#define OP_KEYS \
  _OP_DIAG_KEYS(READ),  \
  _OP_DIAG_KEYS(WRITE)

/* /proc/self/mountstats per-op line order:
 * ops, ntrans, timeouts, bytes_sent, bytes_recv, queue, rtt, execute. */
static int nfs_collect_parse_op_values(char *str, unsigned long long val[8])
{
  int i;
  for (i = 0; i < 8; i++) {
    char *tok = wsep(&str);
    char *end = NULL;
    if (tok == NULL)
      return -1;
    errno = 0;
    val[i] = strtoull(tok, &end, 0);
    if (errno != 0 || end == tok)
      return -1;
  }
  return 0;
}

static void nfs_collect_mnt_op(struct stats *stats, const char *op, char *str)
{
  unsigned long long v[8];
  char key[64];

  if (strcmp(op, "READ") != 0 && strcmp(op, "WRITE") != 0)
    return;
  if (nfs_collect_parse_op_values(str, v) < 0)
    return;

  snprintf(key, sizeof(key), "%s_timeouts", op);
  stats_set(stats, key, v[2]);
  snprintf(key, sizeof(key), "%s_ops", op);
  stats_set(stats, key, v[1]);
  snprintf(key, sizeof(key), "%s_queue", op);
  stats_set(stats, key, v[5]);
  snprintf(key, sizeof(key), "%s_rtt", op);
  stats_set(stats, key, v[6]);
}

#define KEYS EVENT_KEYS, BYTE_KEYS, XPRT_KEYS, OP_KEYS

/* Return 0 if nfs_collect() should re-read *p_line, -1 on EOF / parse error. */

static int nfs_collect_mnt_read_events_xprt_section(struct stats *stats, FILE *file,
						    char **p_line, size_t *p_line_size)
{
  while (1) {
    if (getline(p_line, p_line_size, file) < 0)
      return -1;

    char *rest = *p_line;

    if (*rest != '\t')
      return 0;

    if (strcmp(rest, "\tper-op statistics\n") == 0)
      break;

    char *tag = wsep(&rest);

    /* events: ... bytes: ... xprt: ... */

    if (strcmp(tag, "events:") == 0)
      nfs_collect_mnt_events(stats, rest);
    else if (strcmp(tag, "bytes:") == 0)
      nfs_collect_mnt_bytes(stats, rest);
    else if (strcmp(tag, "xprt:") == 0)
      nfs_collect_mnt_xprt(stats, rest);
  }
  return 1;
}

static int nfs_collect_mnt_read_per_op_section(struct stats *stats, FILE *file,
					       char **p_line, size_t *p_line_size)
{
  while (1) {
    if (getline(p_line, p_line_size, file) < 0)
      return -1;

    char *rest = *p_line;

    if (*rest != '\t')
      return 0;

    char *tag = wsep(&rest);

    char *col = strchr(tag, ':');
    if (col == NULL)
      return -1;
    *col = 0;

    nfs_collect_mnt_op(stats, tag, rest);
  }
}

static int nfs_collect_mnt(struct stats *stats, FILE *file,
			   char **p_line, size_t *p_line_size)
{
  int rc = nfs_collect_mnt_read_events_xprt_section(stats, file, p_line, p_line_size);
  if (rc <= 0)
    return rc;
  return nfs_collect_mnt_read_per_op_section(stats, file, p_line, p_line_size);
}

static inline int strip_crud(char **str, const char *crud)
{
  size_t crud_len = strlen(crud);

  if (strncmp(*str, crud, crud_len) != 0)
    return -1;

  *str += crud_len;

  return 0;
}

static void nfs_collect(struct stats_type *type)
{
  const char *path = "/proc/self/mountstats";
  FILE *file = NULL;
  char file_buf[4096];
  char *line = NULL;
  size_t line_size = 0;

#if !defined(__linux__)
  (void)type;
  return;
#endif

  file = file_fopen_read(path);
  if (file == NULL) {
    if (errno != ENOENT)
      TRACE("nfs: cannot open `%s': %m\n", path);
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  /* device HOST:EXPORT mounted on MNT with fstype nfs statvers=1.0 */

  while (getline(&line, &line_size, file) >= 0) {
    char *rest, *dev, *mnt, *ver;
  skip_getline:
    rest = line;

    if (strip_crud(&rest, "device ") < 0)
      continue;

    dev = wsep(&rest);
    if (dev == NULL || rest == NULL)
      continue;

    if (strip_crud(&rest, "mounted on ") < 0)
      continue;

    /* People who put spaces in their paths deserve what they get. */
    mnt = wsep(&rest);
    if (mnt == NULL || rest == NULL)
      continue;

    if (strip_crud(&rest, "with fstype nfs statvers=") < 0)
      continue;

    ver = wsep(&rest);
    if (strcmp(ver, "1.0") != 0 && strcmp(ver, "1.1") != 0) {
      TRACE("nfs: mount `%s', device `%s' has unknown statvers `%s' (skip)\n",
	    mnt, dev, ver);
      continue;
    }

    TRACE("dev `%s', mnt `%s', ver `%s'\n", dev, mnt, ver);

    struct stats *stats = get_current_stats(type, mnt);
    if (stats == NULL)
      continue;

    if (nfs_collect_mnt(stats, file, &line, &line_size) == 0)
      goto skip_getline;
  }

 out:
  free(line);
  if (file != NULL)
    fclose(file);
}

struct stats_type nfs_stats_type = {
  .st_name = "nfs",
  .st_collect = &nfs_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
