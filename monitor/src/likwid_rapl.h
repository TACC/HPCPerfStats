#ifndef _LIKWID_RAPL_H_
#define _LIKWID_RAPL_H_

#include <stdint.h>

unsigned long long likwid_rapl_raw_to_mj(uint32_t raw, double joules_per_lsb);

int likwid_rapl_is_supported_processor(void);

int likwid_rapl_collect_socket_mj(int cpu_id, unsigned int socket_id,
                                 unsigned long long *pkg_mj,
                                 unsigned long long *core_mj,
                                 unsigned long long *dram_mj,
                                 int *has_pkg, int *has_core, int *has_dram);

#endif
