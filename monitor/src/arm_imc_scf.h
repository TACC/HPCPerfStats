/* arm_imc_scf — Grace SCF uncore helpers for arm_aarch64_imc (pure; no LIKWID). */
#ifndef ARM_IMC_SCF_H
#define ARM_IMC_SCF_H

#define ARM_IMC_SCF_DEVICES_DIR "/sys/bus/event_source/devices"
#define ARM_IMC_SCF_PMU_PREFIX "nvidia_scf_pmu_"

/**
 * Lead CPU of each Grace socket from `<devices_dir>/nvidia_scf_pmu_<N>/cpumask`, ordered by N.
 * @return number of sockets written to @p cpus (0 when none, on bad input, or no SCF PMU).
 */
int arm_imc_scf_lead_cpus(const char *devices_dir, int *cpus, int cap);

/** CMEM_RD_DATA counts 32-byte beats; arm_aarch64_imc CAS keys are 64-byte transfers. */
unsigned long long arm_imc_scf_rd_beats_to_cas(unsigned long long beats);

/** CMEM_WR_TOTAL_BYTES counts bytes. */
unsigned long long arm_imc_scf_wr_bytes_to_cas(unsigned long long bytes);

#endif
