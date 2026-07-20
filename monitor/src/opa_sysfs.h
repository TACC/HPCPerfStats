#ifndef _OPA_SYSFS_H_
#define _OPA_SYSFS_H_

struct stats;

/* Map hfi1 ports/N/counters filename to host_opa schema key, or NULL if unmapped. */
const char *opa_sysfs_schema_key_for_file(const char *sysfs_name);

/* Map hfi1 ports/N/hw_counters filename to host_opa schema key, or NULL if unmapped. */
const char *opa_sysfs_hw_schema_key_for_file(const char *sysfs_name);

/*
 * Read overlapping utilization counters from ports/<port>/counters into stats.
 * If no classic counters are readable (e.g. CN5000 NDR EINVAL stubs), fall back
 * to ports/<port>/hw_counters TxWords/RxWords/TxPkt/RxPkt/TxWait.
 */
int opa_sysfs_collect_port(struct stats *stats, const char *hca, int port);

/* Classic-only collect (no hw fallback). Returns 0 if any key set, else -1. */
int opa_sysfs_collect_classic_counters(struct stats *stats, const char *hca,
                                       int port);

/* hw_counters utilization map only. Returns 0 if any key set, else -1. */
int opa_sysfs_collect_hw_counters(struct stats *stats, const char *hca,
                                  int port);

/* Test hook: override sysfs root (default "/sys"). Pass NULL to restore. */
void opa_sysfs_test_set_root(const char *root);

#endif
