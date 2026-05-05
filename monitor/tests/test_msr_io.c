#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "msr_io.h"

/* msr_open_cpu hard-codes the path "/dev/cpu/<cpu>/msr" so we cannot point it
 * at a tmpdir without root; we exercise msr_read_u64 / msr_write_u64 against a
 * regular tmpfile instead, and check msr_open_cpu's failure path against an
 * obviously bad cpu name. */
int main(void)
{
  char tmpl[] = "/tmp/hps_msr_ioXXXXXX";
  int fd = mkstemp(tmpl);

  assert(fd >= 0);

  /* Pre-fill 4 MSR-sized slots. */
  uint64_t fill[4] = { 0x1111111111111111ULL,
                       0x2222222222222222ULL,
                       0x3333333333333333ULL,
                       0x4444444444444444ULL };

  assert(write(fd, fill, sizeof(fill)) == (ssize_t)sizeof(fill));

  /* Each slot is at offset N * 8 bytes. */
  uint64_t v;

  assert(msr_read_u64(fd, 0, &v) == 0);
  assert(v == fill[0]);
  assert(msr_read_u64(fd, 8, &v) == 0);
  assert(v == fill[1]);
  assert(msr_read_u64(fd, 24, &v) == 0);
  assert(v == fill[3]);

  /* Write replaces a slot. */
  assert(msr_write_u64(fd, 16, 0xdeadbeefcafef00dULL) == 0);
  assert(msr_read_u64(fd, 16, &v) == 0);
  assert(v == 0xdeadbeefcafef00dULL);

  /* Read past EOF: depending on platform, read returns short or 0; msr_read_u64
   * surfaces this as -1 with EIO. */
  int rc = msr_read_u64(fd, 1024, &v);

  assert(rc == -1);

  close(fd);
  unlink(tmpl);

  /* msr_open_cpu fails for missing /dev/cpu/<cpu>/msr nodes. */
  int bad = msr_open_cpu("zzzzz_no_such_cpu", O_RDONLY);
  assert(bad < 0);

  /* NULL cpu argument is rejected. */
  bad = msr_open_cpu(NULL, O_RDONLY);
  assert(bad < 0);

  puts("test_msr_io passed");
  return 0;
}
