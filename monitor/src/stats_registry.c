/* Central registry of struct stats_type pointers sorted by st_name. */
#include "stats_registry.h"

#include "stats.h"

#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_LIKWID)
extern struct stats_type amd_x86_uncore_df_rome_stats_type;
extern struct stats_type amd_x86_uncore_df_milan_stats_type;
extern struct stats_type amd_x86_uncore_df_genoa_stats_type;
extern struct stats_type amd_x86_uncore_df_turin_stats_type;
extern struct stats_type amd64_rapl_stats_type;
extern struct stats_type intel_rapl_stats_type;
extern struct stats_type intel_skx_cha_stats_type;
extern struct stats_type intel_skx_imc_stats_type;
extern struct stats_type intel_icx_imc_stats_type;
extern struct stats_type intel_spr_imc_stats_type;
extern struct stats_type intel_emr_imc_stats_type;
extern struct stats_type intel_gnr_imc_stats_type;
extern struct stats_type intel_srf_imc_stats_type;
#endif

#if defined(MONITOR_WITH_AMD_GPU)
extern struct stats_type amd_gpu_stats_type;
#endif

#if defined(MONITOR_WITH_INTEL_GPU)
extern struct stats_type intel_gpu_stats_type;
#endif

#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_DCGM) &&                         \
    defined(MONITOR_HOST_IS_ARM)
extern struct stats_type arm_imc_stats_type;
#endif

extern struct stats_type block_stats_type;
extern struct stats_type cpu_stats_type;

#if defined(MONITOR_WITH_HARDWARE) &&                                                              \
    (defined(MONITOR_CPU_BACKEND_LIKWID) || defined(MONITOR_CPU_BACKEND_DCGM))
extern struct stats_type cpu_counter_metrics_stats_type;
#endif

extern struct stats_type ib_stats_type;

#if defined(MONITOR_WITH_BEEGFS)
extern struct stats_type beegfs_stats_type;
#endif

#if defined(MONITOR_WITH_LUSTRE)
extern struct stats_type llite_stats_type;
extern struct stats_type mdc_stats_type;
extern struct stats_type osc_stats_type;
#endif

extern struct stats_type lnet_stats_type;

extern struct stats_type mem_stats_type;

extern struct stats_type net_stats_type;
extern struct stats_type nfs_stats_type;
extern struct stats_type numa_stats_type;

#if defined(MONITOR_WITH_GPU)
extern struct stats_type nvidia_gpu_stats_type;
#endif

extern struct stats_type opa_stats_type;

extern struct stats_type proc_stats_type;
extern struct stats_type ps_stats_type;
extern struct stats_type roofline_hw_peak_stats_type;
extern struct stats_type sysv_shm_stats_type;
extern struct stats_type tmpfs_stats_type;
extern struct stats_type vfs_stats_type;
extern struct stats_type vm_stats_type;

struct stats_type *const stats_type_table[] = {
#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_LIKWID)
    &amd_x86_uncore_df_genoa_stats_type,
    &amd_x86_uncore_df_milan_stats_type,
    &amd_x86_uncore_df_rome_stats_type,
    &amd_x86_uncore_df_turin_stats_type,
    &amd64_rapl_stats_type,
#endif
#if defined(MONITOR_WITH_AMD_GPU)
    &amd_gpu_stats_type,
#endif
#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_DCGM) &&                         \
    defined(MONITOR_HOST_IS_ARM)
    &arm_imc_stats_type,
#endif
#if defined(MONITOR_WITH_BEEGFS)
    &beegfs_stats_type,
#endif
    &block_stats_type,
    &cpu_stats_type,
#if defined(MONITOR_WITH_HARDWARE) &&                                                              \
    (defined(MONITOR_CPU_BACKEND_LIKWID) || defined(MONITOR_CPU_BACKEND_DCGM))
    &cpu_counter_metrics_stats_type,
#endif
    &ib_stats_type,
#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_LIKWID)
    &intel_rapl_stats_type,
    &intel_skx_cha_stats_type,
    &intel_emr_imc_stats_type,
    &intel_gnr_imc_stats_type,
    &intel_icx_imc_stats_type,
    &intel_skx_imc_stats_type,
    &intel_spr_imc_stats_type,
    &intel_srf_imc_stats_type,
#endif
#if defined(MONITOR_WITH_INTEL_GPU)
    &intel_gpu_stats_type,
#endif
#if defined(MONITOR_WITH_LUSTRE)
    &llite_stats_type,
    &mdc_stats_type,
    &osc_stats_type,
#endif
    &lnet_stats_type,
    &mem_stats_type,
    &net_stats_type,
    &nfs_stats_type,
    &numa_stats_type,
#if defined(MONITOR_WITH_GPU)
    &nvidia_gpu_stats_type,
#endif
    &opa_stats_type,
    &proc_stats_type,
    &ps_stats_type,
    &roofline_hw_peak_stats_type,
    &sysv_shm_stats_type,
    &tmpfs_stats_type,
    &vfs_stats_type,
    &vm_stats_type,
};

const size_t stats_type_nr = sizeof(stats_type_table) / sizeof(stats_type_table[0]);
