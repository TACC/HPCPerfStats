#ifndef _OPA_SYSFS_H_
#define _OPA_SYSFS_H_

struct stats;

/* Map hfi1 ports/N/counters filename to host_opa schema key, or NULL if unmapped. */
const char *opa_sysfs_schema_key_for_file(const char *sysfs_name);

/* Read overlapping utilization counters from ports/<port>/counters into stats. */
int opa_sysfs_collect_port(struct stats *stats, const char *hca, int port);

#endif
