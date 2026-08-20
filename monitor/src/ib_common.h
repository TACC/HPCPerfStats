#ifndef _IB_COMMON_H_
#define _IB_COMMON_H_

typedef void (*ib_hca_port_fn)(const char *hca, int port, void *ctx);

/* Omni-Path / Cornelis HFI HCAs (hfi1_*) belong to host_opa, not host_ib. */
int ib_hca_is_opa_hfi(const char *hca);
/* Nonzero if /sys/class/infiniband has at least one OPA HFI. */
int ib_sysfs_has_opa_hfi(void);
/* Parse unit index from hfi1_<N> (or bare hfi1 → 0). Returns -1 if not an HFI name. */
int opa_hfi_unit_from_name(const char *hca);

int ib_port_collectible(const char *hca, int port);
void ib_foreach_hca_port(ib_hca_port_fn fn, void *ctx);

#endif
