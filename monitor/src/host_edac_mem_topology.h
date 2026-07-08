#ifndef HOST_EDAC_MEM_TOPOLOGY_H_
#define HOST_EDAC_MEM_TOPOLOGY_H_

typedef void (*host_edac_dimm_fn)(long long mtps, int is_hbm, void *ctx);

/* Scan EDAC dimm_mem_type; sets *has_ddr / *has_hbm when dimm_mem_speed > 0. */
int host_edac_scan_mem_classes(int *has_ddr, int *has_hbm);

/* Invoke fn for each dimm with valid dimm_mem_speed (for roofline bandwidth). */
int host_edac_foreach_dimm(host_edac_dimm_fn fn, void *ctx);

#endif
