/* BeeGFS TCP-counter reader: peer parsing from the client's node listing, the monotonic
 * accumulator across reconnects, and a live NETLINK_SOCK_DIAG read of a loopback connection. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "beegfs_tcp.h"

static void test_parse_peers(void)
{
  const char *text = "storageB [ID: 1]\n"
                     "   Connections: TCP: 16 (192.168.40.14:8201); \n"
                     "storageA [ID: 2]\n"
                     "   Connections: TCP: 16 (192.168.40.13:8101 [fallback route]); \n"
                     "storageC [ID: 3]\n"
                     "   Connections: <none>\n";
  struct beegfs_tcp_peer p[4];
  struct in_addr a;
  int untracked = -1;

  assert(beegfs_tcp_parse_peers(text, p, 4, &untracked) == 2 && untracked == 0);
  assert(inet_pton(AF_INET, "192.168.40.14", &a) == 1 && p[0].addr == a.s_addr);
  assert(p[0].port == 8201 && p[0].conns == 16);
  assert(inet_pton(AF_INET, "192.168.40.13", &a) == 1 && p[1].addr == a.s_addr);
  assert(p[1].port == 8101 && p[1].conns == 16);
  assert(beegfs_tcp_parse_peers(text, p, 1, &untracked) == 1 && untracked == 1);
  assert(beegfs_tcp_parse_peers("storageC [ID: 3]\n   Connections: <none>\n", p, 4, &untracked) ==
             0 &&
         untracked == 0);
}

/* Listings whose traffic the TCP counters cannot see (or cannot fully see) are flagged, so the
 * caller publishes a code instead of a zero or partial total. */
static void test_parse_peers_untracked(void)
{
  static const char *const cases[] = {
      "s [ID: 1]\n   Connections: RDMA: 4 (192.168.40.14:8201); \n",
      "s [ID: 1]\n   Connections: TCP: 1 (192.168.40.14:8201); RDMA: 4 (192.168.40.14:8201); \n",
      "s [ID: 1]\n   Connections: SDP: 2 (192.168.40.14:8201); \n",
      "s [ID: 1]\n   Connections: TCP: 16; \n",
      "s [ID: 1]\n   Connections not available [Error: Out of memory]\n",
      "s [ID: 1]\n   Connections: garbage\n",
      "s [ID: 1]\n   Connections: TCP: 1 (bad:addr); \n",
      "s [ID: 1]\n   Connections: TCP: 1 (10.0.0.1:0); \n",
      "s [ID: 1]\n   Connections: TCP: 1 (10.0.0.1:70000); \n",
      "no nodes listed",
      ""};
  struct beegfs_tcp_peer p[4];
  int untracked;

  for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
    untracked = 0;
    (void)beegfs_tcp_parse_peers(cases[i], p, 4, &untracked);
    assert(untracked == 1);
  }
  untracked = 0;
  assert(beegfs_tcp_parse_peers(NULL, p, 4, &untracked) == 0 && untracked == 1);
  assert(beegfs_tcp_parse_peers("s [ID: 1]\n   Connections: <none>\n", p, 4, NULL) == 0);
}

/* Totals only grow: a socket adds its growth, a new socket its full counters, and a socket that
 * disappeared (reconnect) leaves the totals where they were. */
static void test_accumulator(void)
{
  static struct beegfs_tcp_acc acc;
  struct beegfs_tcp_sock s[2] = {{.cookie = 1, .acked = 100, .received = 10},
                                 {.cookie = 2, .acked = 50, .received = 5}};

  memset(&acc, 0, sizeof(acc));
  beegfs_tcp_acc_update(&acc, s, 2);
  assert(acc.tx == 150 && acc.rx == 15);

  s[0].acked = 130;
  s[0].received = 12;
  beegfs_tcp_acc_update(&acc, s, 1);
  assert(acc.tx == 180 && acc.rx == 17);

  s[0].cookie = 3;
  s[0].acked = 7;
  s[0].received = 1;
  beegfs_tcp_acc_update(&acc, s, 1);
  assert(acc.tx == 187 && acc.rx == 18);

  s[0].acked = 2;
  beegfs_tcp_acc_update(&acc, s, 1);
  assert(acc.tx == 189 && acc.rx == 19);

  beegfs_tcp_acc_update(&acc, NULL, 0);
  assert(acc.tx == 189 && acc.rx == 19 && acc.n == 0);
}

/* A loopback client sends a known byte count; the reader finds the client socket by the
 * listener's endpoint and its bytes_acked covers what was sent. */
static void test_dump_loopback(void)
{
  struct sockaddr_in sa;
  socklen_t sl = sizeof(sa);
  struct beegfs_tcp_peer peer;
  struct beegfs_tcp_sock socks[8];
  static char payload[100000];
  char sink[65536];
  size_t got = 0;
  size_t n = 0;
  int lfd, cfd, afd;

  memset(&sa, 0, sizeof(sa));
  sa.sin_family = AF_INET;
  sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  lfd = socket(AF_INET, SOCK_STREAM, 0);
  assert(lfd >= 0 && bind(lfd, (struct sockaddr *)&sa, sizeof(sa)) == 0 && listen(lfd, 1) == 0);
  assert(getsockname(lfd, (struct sockaddr *)&sa, &sl) == 0);
  cfd = socket(AF_INET, SOCK_STREAM, 0);
  assert(cfd >= 0 && connect(cfd, (struct sockaddr *)&sa, sizeof(sa)) == 0);
  afd = accept(lfd, NULL, NULL);
  assert(afd >= 0);
  assert(send(cfd, payload, sizeof(payload), 0) == (ssize_t)sizeof(payload));
  while (got < sizeof(payload)) {
    ssize_t r = recv(afd, sink, sizeof(sink), 0);

    assert(r > 0);
    got += (size_t)r;
  }

  peer.addr = sa.sin_addr.s_addr;
  peer.port = ntohs(sa.sin_port);
  if (beegfs_tcp_dump(&peer, 1, socks, 8, &n) != 0) {
    /* qemu-user (and a kernel without sock_diag) has no NETLINK_SOCK_DIAG. */
    assert(errno == EOPNOTSUPP && n == 0);
    printf("test_beegfs_tcp: no NETLINK_SOCK_DIAG (emulated?), skipping the live dump check\n");
    close(afd);
    close(cfd);
    close(lfd);
    return;
  }
  for (int tries = 0; tries < 100; tries++) {
    const struct timespec tick = {0, 10000000L};

    assert(beegfs_tcp_dump(&peer, 1, socks, 8, &n) == 0);
    if (n == 1 && socks[0].acked >= sizeof(payload))
      break;
    nanosleep(&tick, NULL);
  }
  assert(n == 1);
  assert(socks[0].acked >= sizeof(payload));
  errno = 0;
  assert(beegfs_tcp_dump(&peer, 1, socks, 0, &n) < 0 && errno == ENOBUFS && n == 0);
  errno = 0;
  assert(beegfs_tcp_dump(NULL, 1, socks, 8, &n) < 0 && errno == EINVAL);
  close(afd);
  close(cfd);
  close(lfd);
}

int main(void)
{
  test_parse_peers();
  test_parse_peers_untracked();
  test_accumulator();
  test_dump_loopback();
  puts("test_beegfs_tcp: OK");
  return 0;
}
