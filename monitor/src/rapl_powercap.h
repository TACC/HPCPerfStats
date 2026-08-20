/*! \file rapl_powercap.h
 *  RAPL energy via /sys/class/powercap (PERF fallback when LIKWID PWR is flat).
 */

#ifndef _RAPL_POWERCAP_H_
#define _RAPL_POWERCAP_H_

#include <stdint.h>

#define RAPL_POWERCAP_DEFAULT_ROOT "/sys/class/powercap"

/* energy_uj → schema millijoules (round nearest). */
unsigned long long rapl_powercap_uj_to_mj(unsigned long long uj);

/*
 * Parse powercap domain name "package-N" → socket id.
 * Returns 0 on success, -1 if not a package name.
 */
int rapl_powercap_parse_package_id(const char *name, unsigned *socket_out);

/*
 * Map child domain name to schema key: core→pp0/core, dram→dram, uncore ignored.
 * amd_path nonzero → core maps to core_energy; else pp0_energy.
 * Returns NULL when the name is not a mapped energy domain.
 */
const char *rapl_powercap_schema_key_from_name(const char *name, int amd_path);

/* Nonzero if powercap_root has at least one intel-rapl/amd-rapl package dir. */
int rapl_powercap_available_under(const char *powercap_root);

int rapl_powercap_available(void);

/*
 * Read cumulative energy for socket_id under powercap_root.
 * Sets has_* only for domains that were read successfully.
 * Returns 0 when at least one domain was read, -1 otherwise.
 */
int rapl_powercap_collect_socket_mj_under(const char *powercap_root, unsigned socket_id,
                                          unsigned long long *pkg_mj, unsigned long long *core_mj,
                                          unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                          int *has_dram, unsigned long long *pp1_mj, int *has_pp1,
                                          int amd_path);

int rapl_powercap_collect_socket_mj(unsigned socket_id, unsigned long long *pkg_mj,
                                    unsigned long long *core_mj, unsigned long long *dram_mj,
                                    int *has_pkg, int *has_core, int *has_dram,
                                    unsigned long long *pp1_mj, int *has_pp1, int amd_path);

#endif
