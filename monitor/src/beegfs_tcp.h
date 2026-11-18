#ifndef BEEGFS_TCP_H_
#define BEEGFS_TCP_H_

#include <stddef.h>
#include <stdint.h>

/* BeeGFS client I/O bytes from the kernel's per-socket TCP counters on the client's connections
 * to its storage servers, read through NETLINK_SOCK_DIAG (no fork, no server request). */

#define BEEGFS_TCP_MAX_PEERS 128
#define BEEGFS_TCP_MAX_SOCKS 512

/* A server endpoint the client connects to: IPv4 address in network order, port in host order,
 * and the client's count of established TCP connections to that server. */
struct beegfs_tcp_peer {
  uint32_t addr;
  uint16_t port;
  uint32_t conns;
};

/* One established connection: kernel socket cookie, bytes the peer acknowledged (sent) and
 * bytes received. */
struct beegfs_tcp_sock {
  uint64_t cookie;
  uint64_t acked;
  uint64_t received;
};

/* Monotonic totals: each socket's growth since the previous update is added, so a reconnect
 * loses at most the closed socket's bytes since that update. */
struct beegfs_tcp_acc {
  unsigned long long tx;
  unsigned long long rx;
  size_t n;
  struct beegfs_tcp_sock prev[BEEGFS_TCP_MAX_SOCKS];
};

/*! Parse the TCP endpoints of a client `storage_nodes` / `meta_nodes` listing (one
 *  `<alias> [ID: n]` block with one `Connections:` line per node). *untracked is set when the
 *  TCP counters cannot account for the client's traffic: no node listed, a node without a
 *  readable `Connections:` line, an RDMA or SDP connection, a TCP count without an endpoint, a
 *  truncated line, or more endpoints than max.
 *  @return number of peers written to out (at most max) */
size_t beegfs_tcp_parse_peers(const char *text, struct beegfs_tcp_peer *out, size_t max,
                              int *untracked);

/*! Read the established IPv4 TCP sockets whose remote endpoint is one of `peers`.
 *  @return 0 with *n_out sockets in out; -1 with errno set (EOPNOTSUPP when the kernel has no
 *  NETLINK_SOCK_DIAG or its tcp_info has no byte counters, ENOBUFS when more than max sockets match, EPROTO or EMSGSIZE
 *  for a malformed or truncated reply) */
int beegfs_tcp_dump(const struct beegfs_tcp_peer *peers, size_t npeers, struct beegfs_tcp_sock *out,
                    size_t max, size_t *n_out);

/*! Add the growth of `cur` (this update's sockets) to the totals and remember it. A socket not
 *  seen before, or whose counters went backwards, adds its full counters. */
void beegfs_tcp_acc_update(struct beegfs_tcp_acc *acc, const struct beegfs_tcp_sock *cur, size_t n);

#endif
