#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "intel_mmconfig.h"

/* SKIP (non-root unit test): intel_mmconfig_open mmap()s /dev/mem and needs
 * CAP_SYS_RAWIO. CI runners and developer laptops without /dev/mem access
 * cannot exercise the happy path here — do not treat open() failure as a
 * regression. Coverage in this driver is limited to:
 *   - intel_mmconfig_close on never-opened / NULL structs (always run)
 *   - intel_mmconfig_open best-effort probe (succeeds only when privileged)
 */
int main(void)
{
  struct intel_mmconfig empty = { -1, MAP_FAILED, 0, 0 };

  /* Closing a never-opened struct is a no-op. */
  intel_mmconfig_close(&empty);
  assert(empty.fd == -1);
  assert(empty.map == MAP_FAILED);

  /* NULL is rejected. */
  intel_mmconfig_close(NULL);

  /* Open a small synthetic mapping via memfd: we route through the public
   * helper by also constructing an alternate path? Not possible: helper
   * hard-codes /dev/mem. So just assert that a regular non-root invocation
   * either succeeds (if running with permissions) or returns -1 cleanly. */
  struct intel_mmconfig mm = { -1, MAP_FAILED, 0, 0 };
  int rc = intel_mmconfig_open(&mm, 0x80000000, 4096);

  if (rc == 0) {
    /* Surprised but fine: tear down. */
    intel_mmconfig_close(&mm);
    assert(mm.fd == -1);
    assert(mm.map == MAP_FAILED);
  } else {
    /* Expected on most CI hosts: helper kept the struct in a clean state. */
    assert(mm.fd == -1);
    assert(mm.map == MAP_FAILED);
  }

  /* NULL out param is rejected. */
  rc = intel_mmconfig_open(NULL, 0, 0);
  assert(rc == -1);

  puts("test_intel_mmconfig passed");
  return 0;
}
