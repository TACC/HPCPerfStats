#ifndef _VARIORUM_RAPL_H_
#define _VARIORUM_RAPL_H_

int variorum_rapl_is_supported_processor(void);
int variorum_rapl_collect_socket_mj(unsigned int socket_id,
                                    unsigned long long *pkg_mj,
                                    unsigned long long *core_mj,
                                    unsigned long long *dram_mj,
                                    int *has_pkg,
                                    int *has_core,
                                    int *has_dram);
int variorum_rapl_parse_socket_mj(const char *energy_json,
                                  unsigned int socket_id,
                                  unsigned long long *pkg_mj,
                                  unsigned long long *core_mj,
                                  unsigned long long *dram_mj,
                                  int *has_pkg,
                                  int *has_core,
                                  int *has_dram);

#endif
