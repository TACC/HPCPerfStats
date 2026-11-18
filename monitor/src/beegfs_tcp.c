/* BeeGFS client I/O bytes from the kernel TCP counters on its storage connections. */
#include "beegfs_tcp.h"

#include <arpa/inet.h>
#include <errno.h>
#include <linux/inet_diag.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <linux/sock_diag.h>
#include <linux/tcp.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define BEEGFS_TCP_RECV_BUF 32768
#define BEEGFS_TCP_LINE_MAX 512
#define BEEGFS_TCP_STATE_ESTABLISHED 1 /* TCP_ESTABLISHED in the kernel's tcp_states.h */

/* Parse one `Connections:` value: `<none>` or `TYPE: count (ip:port); ...` per transport; any
 * other text flags the listing untracked. */
static size_t beegfs_tcp_parse_conns(const char *p, struct beegfs_tcp_peer *out, size_t max,
                                     int *untracked)
{
  size_t n = 0;
  int segments = 0;
  char type[8];
  char ip[INET_ADDRSTRLEN];
  unsigned count;
  unsigned port;
  int used;

  if (strncmp(p + strspn(p, " "), "<none>", 6) == 0)
    return 0;
  while (p != NULL && sscanf(p, " %7[A-Z]: %u%n", type, &count, &used) == 2) {
    struct in_addr a;

    p += used;
    segments++;
    if (strcmp(type, "TCP") == 0 && n < max && sscanf(p, " (%15[0-9.]:%u", ip, &port) == 2 &&
        port > 0 && port <= 65535 && inet_pton(AF_INET, ip, &a) == 1) {
      out[n].addr = a.s_addr;
      out[n].port = (uint16_t)port;
      out[n].conns = count;
      n++;
    } else {
      *untracked = 1;
    }
    p = strchr(p, ';');
    if (p != NULL)
      p++;
  }
  if (segments == 0)
    *untracked = 1;
  return n;
}

size_t beegfs_tcp_parse_peers(const char *text, struct beegfs_tcp_peer *out, size_t max,
                              int *untracked)
{
  static const char tag[] = "Connections:";
  char line[BEEGFS_TCP_LINE_MAX];
  size_t n = 0;
  size_t nodes = 0;
  size_t conn_lines = 0;
  const char *p;

  if (untracked == NULL)
    return 0;
  *untracked = 1;
  if (text == NULL || out == NULL)
    return 0;
  for (p = strstr(text, "[ID: "); p != NULL; p = strstr(p + 1, "[ID: "))
    nodes++;
  *untracked = 0;
  for (p = strstr(text, tag); p != NULL; p = strstr(p, tag)) {
    size_t len;

    p += sizeof(tag) - 1;
    len = strcspn(p, "\n");
    if (len >= sizeof(line))
      *untracked = 1;
    snprintf(line, sizeof(line), "%.*s", (int)len, p);
    n += beegfs_tcp_parse_conns(line, out + n, max - n, untracked);
    conn_lines++;
  }
  if (nodes == 0 || conn_lines != nodes)
    *untracked = 1;
  return n;
}

static int beegfs_tcp_peer_match(const struct beegfs_tcp_peer *peers, size_t npeers,
                                 const struct inet_diag_msg *m)
{
  for (size_t i = 0; i < npeers; i++)
    if (peers[i].addr == m->id.idiag_dst[0] && peers[i].port == ntohs(m->id.idiag_dport))
      return 1;
  return 0;
}

/* The tcp_info attribute of one dump record; NULL when absent or too short for the byte
 * counters (kernels before 4.2). */
static struct tcp_info *beegfs_tcp_info(struct inet_diag_msg *m, int attr_len)
{
  struct rtattr *rta = (struct rtattr *)(m + 1);

  for (; RTA_OK(rta, attr_len); rta = RTA_NEXT(rta, attr_len))
    if (rta->rta_type == INET_DIAG_INFO &&
        RTA_PAYLOAD(rta) >= offsetof(struct tcp_info, tcpi_bytes_received) + sizeof(__u64))
      return RTA_DATA(rta);
  return NULL;
}

static int beegfs_tcp_request(int fd)
{
  struct {
    struct nlmsghdr nlh;
    struct inet_diag_req_v2 req;
  } msg;
  struct sockaddr_nl sa;

  memset(&msg, 0, sizeof(msg));
  memset(&sa, 0, sizeof(sa));
  sa.nl_family = AF_NETLINK;
  msg.nlh.nlmsg_len = sizeof(msg);
  msg.nlh.nlmsg_type = SOCK_DIAG_BY_FAMILY;
  msg.nlh.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
  msg.req.sdiag_family = AF_INET;
  msg.req.sdiag_protocol = IPPROTO_TCP;
  msg.req.idiag_states = 1U << BEEGFS_TCP_STATE_ESTABLISHED;
  msg.req.idiag_ext = 1U << (INET_DIAG_INFO - 1);
  return sendto(fd, &msg, sizeof(msg), 0, (struct sockaddr *)&sa, sizeof(sa)) < 0 ? -1 : 0;
}

/* Collect the matching sockets of one recv() batch. @return 1 at NLMSG_DONE, 0 for more, -1 on
 * error (errno set: EPROTO malformed, ENOBUFS more matches than max). */
static int beegfs_tcp_batch(char *buf, size_t len, const struct beegfs_tcp_peer *peers,
                            size_t npeers, struct beegfs_tcp_sock *out, size_t max, size_t *n)
{
  for (size_t off = 0; off + sizeof(struct nlmsghdr) <= len;) {
    struct nlmsghdr *h = (struct nlmsghdr *)(buf + off);
    struct inet_diag_msg *m = NLMSG_DATA(h);
    struct tcp_info *ti;

    if (h->nlmsg_len < sizeof(*h) || h->nlmsg_len > len - off ||
        (h->nlmsg_type == NLMSG_ERROR && h->nlmsg_len < NLMSG_LENGTH(sizeof(struct nlmsgerr)))) {
      errno = EPROTO;
      return -1;
    }
    off += NLMSG_ALIGN(h->nlmsg_len);
    if (h->nlmsg_type == NLMSG_DONE)
      return 1;
    if (h->nlmsg_type == NLMSG_ERROR) {
      const struct nlmsgerr *e = NLMSG_DATA(h);

      errno = e->error != 0 ? -e->error : EPROTO;
      return -1;
    }
    if (h->nlmsg_len < NLMSG_LENGTH(sizeof(*m)) || !beegfs_tcp_peer_match(peers, npeers, m))
      continue;
    ti = beegfs_tcp_info(m, (int)(h->nlmsg_len - NLMSG_LENGTH(sizeof(*m))));
    if (ti == NULL) {
      errno = EOPNOTSUPP;
      return -1;
    }
    if (*n == max) {
      errno = ENOBUFS;
      return -1;
    }
    out[*n].cookie = ((uint64_t)m->id.idiag_cookie[1] << 32) | m->id.idiag_cookie[0];
    out[*n].acked = ti->tcpi_bytes_acked;
    out[*n].received = ti->tcpi_bytes_received;
    (*n)++;
  }
  return 0;
}

int beegfs_tcp_dump(const struct beegfs_tcp_peer *peers, size_t npeers, struct beegfs_tcp_sock *out,
                    size_t max, size_t *n_out)
{
  static char buf[BEEGFS_TCP_RECV_BUF];
  int fd;
  int rc = 0;
  size_t n = 0;

  if (peers == NULL || out == NULL || n_out == NULL) {
    errno = EINVAL;
    return -1;
  }
  *n_out = 0;
  fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC, NETLINK_SOCK_DIAG);
  if (fd < 0 && errno == EPROTONOSUPPORT)
    errno = EOPNOTSUPP;
  if (fd < 0)
    return -1;
  if (beegfs_tcp_request(fd) != 0)
    rc = -1;
  while (rc == 0) {
    ssize_t len = recv(fd, buf, sizeof(buf), MSG_TRUNC);

    if (len < 0 && errno == EINTR)
      continue;
    if (len <= 0 || (size_t)len > sizeof(buf)) {
      if (len >= 0)
        errno = len == 0 ? EPROTO : EMSGSIZE;
      rc = -1;
      break;
    }
    rc = beegfs_tcp_batch(buf, (size_t)len, peers, npeers, out, max, &n);
  }
  close(fd);
  if (rc < 0)
    return -1;
  *n_out = n;
  return 0;
}

void beegfs_tcp_acc_update(struct beegfs_tcp_acc *acc, const struct beegfs_tcp_sock *cur, size_t n)
{
  if (acc == NULL || (cur == NULL && n > 0))
    return;
  if (n > BEEGFS_TCP_MAX_SOCKS)
    n = BEEGFS_TCP_MAX_SOCKS;
  for (size_t i = 0; i < n; i++) {
    const struct beegfs_tcp_sock *prev = NULL;

    for (size_t j = 0; j < acc->n && prev == NULL; j++)
      if (acc->prev[j].cookie == cur[i].cookie)
        prev = &acc->prev[j];
    if (prev != NULL && cur[i].acked >= prev->acked && cur[i].received >= prev->received) {
      acc->tx += cur[i].acked - prev->acked;
      acc->rx += cur[i].received - prev->received;
    } else {
      acc->tx += cur[i].acked;
      acc->rx += cur[i].received;
    }
  }
  if (n > 0)
    memcpy(acc->prev, cur, n * sizeof(*cur));
  acc->n = n;
}
