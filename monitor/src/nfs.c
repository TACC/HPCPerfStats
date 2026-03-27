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
  X(inode_revalidate,  "E", ""), \
  X(dentry_revalidate, "E", ""), \
  X(data_invalidate,   "E", ""), \
  X(attr_invalidate,   "E", ""), \
  X(vfs_open,          "E", ""), \
  X(vfs_lookup,        "E", ""), \
  X(vfs_access,        "E", ""), \
  X(vfs_updatepage,    "E", ""), \
  X(vfs_readpage,      "E", ""), \
  X(vfs_readpages,     "E", ""), \
  X(vfs_writepage,     "E", ""), \
  X(vfs_writepages,    "E", ""), \
  X(vfs_getdents,      "E", ""), \
  X(vfs_setattr,       "E", ""), \
  X(vfs_flush,         "E", ""), \
  X(vfs_fsync,         "E", ""), \
  X(vfs_lock,          "E", ""), \
  X(vfs_release,       "E", ""), \
  X(congestion_wait,   "E", ""), \
  X(setattr_trunc,     "E", ""), \
  X(extend_write,      "E", ""), \
  X(silly_rename,      "E", ""), \
  X(short_read,        "E", ""), \
  X(short_write,       "E", ""), \
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
  X(server_write, "E,U=B", ""), \
  X(read_page,    "E",     ""), \
  X(write_page,   "E",     "")

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
  X(xprt_sends,    "E", ""), \
  X(xprt_recvs,    "E", ""), \
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

#define _OP_KEY(o) \
  X(o##_ops,        "E",      "count of "#o" oprations"), \
  X(o##_ntrans,     "E",      "count of "#o" RPC transmissions"), \
  X(o##_timeouts,   "E",      "count of "#o" major timeouts"), \
  X(o##_bytes_sent, "E,U=B",  "bytes sent for "#o), \
  X(o##_bytes_recv, "E,U=B",  "bytes received for "#o), \
  X(o##_queue,      "E,U=ms", "time "#o" RPC queued for send"), \
  X(o##_rtt,        "E,U=ms", "RTT for "#o" RPC"), \
  X(o##_execute,    "E,U=ms", "time executing "#o" RPC")

#define OP_KEYS \
  _OP_KEY(ACCESS),      \
  _OP_KEY(COMMIT),      \
  _OP_KEY(CREATE),      \
  _OP_KEY(FSINFO),      \
  _OP_KEY(FSSTAT),      \
  _OP_KEY(GETATTR),     \
  _OP_KEY(LINK),        \
  _OP_KEY(LOOKUP),      \
  _OP_KEY(MKDIR),       \
  _OP_KEY(MKNOD),       \
  _OP_KEY(PATHCONF),    \
  _OP_KEY(READ),        \
  _OP_KEY(READDIR),     \
  _OP_KEY(READDIRPLUS), \
  _OP_KEY(READLINK),    \
  _OP_KEY(REMOVE),      \
  _OP_KEY(RENAME),      \
  _OP_KEY(RMDIR),       \
  _OP_KEY(SETATTR),     \
  _OP_KEY(SYMLINK),     \
  _OP_KEY(WRITE)

static void nfs_collect_mnt_op(struct stats *stats, const char *op, char *str)
{
  str_collect_prefix_key_list(str, stats, op,
			      "_ops", "_ntrans", "_timeouts",
			      "_bytes_sent", "_bytes_recv",
			      "_queue", "_rtt", "_execute",
			      NULL);
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
