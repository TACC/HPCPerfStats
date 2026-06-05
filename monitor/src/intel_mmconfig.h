#ifndef _INTEL_MMCONFIG_H_
#define _INTEL_MMCONFIG_H_

#include <stdint.h>

/* /dev/mem MMCONFIG region helpers shared by Intel SKX uncore drivers.
 *
 * intel_skx_imc.c and related PCI MMIO collectors repeated the same
 * scaffolding to mmap the PCI MMCONFIG window, then unmap and close on the
 * way out. intel_mmconfig_open returns a populated struct that the caller
 * passes to intel_mmconfig_close.
 *
 * The base address and window size are exposed as fields so the caller can
 * still compute its own DID-relative offsets.
 */

struct intel_mmconfig {
  int fd;
  uint32_t *map;
  uint64_t base;
  uint64_t size;
};

/* Open /dev/mem and mmap the [base, base + size) MMCONFIG window. The mapping
 * is PROT_READ | PROT_WRITE | MAP_SHARED. Honours path_open_fail_once.
 *
 * Returns 0 on success and fills out->fd / out->map. On failure, sets
 * out->fd = -1 and out->map = MAP_FAILED, returns -1, errno preserved. */
int intel_mmconfig_open(struct intel_mmconfig *out, uint64_t base, uint64_t size);

/* Tear down a successfully-opened mmconfig. Safe to call on a half-initialised
 * struct as long as fd was initialised to -1 and map to MAP_FAILED. */
void intel_mmconfig_close(struct intel_mmconfig *m);

#endif
