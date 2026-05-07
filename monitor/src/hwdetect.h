#ifndef _HWDETECT_H_
#define _HWDETECT_H_

void auto_disable_optional_stats_by_lspci(void);
void hwdetect_probe_optional_stack_presence(int *has_nvidia_gpu,
                                            int *has_amd_gpu,
                                            int *has_ib,
                                            int *has_opa);

#endif
