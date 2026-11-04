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

/** How arm_aarch64_imc reads DRAM CAS. PENDING: SCF PMUs exist but the overlay group is not up
 * yet (it begins inside the DCGM backend, which can be deferred); collect attaches later. */
enum arm_imc_mode {
  ARM_IMC_MODE_DISABLED = 0,
  ARM_IMC_MODE_PERF,
  ARM_IMC_MODE_SCF,
  ARM_IMC_MODE_PENDING,
};

/** Mode at begin: SCF when the overlay carries SCF events, else the perf dmc/imc scan, else
 * PENDING when an overlay build has SCF PMUs, else DISABLED. */
int arm_imc_scf_begin_mode(int overlay_build, int has_scf, int n_scf_leads, int n_perf_devs);

/** Mode at collect: PENDING becomes SCF once the overlay has SCF events; others are final. */
int arm_imc_scf_collect_mode(int cur, int has_scf, int n_scf_leads);

#endif
