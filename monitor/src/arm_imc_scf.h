/* arm_imc_scf — Grace SCF uncore DRAM traffic for arm_aarch64_imc via direct perf (no LIKWID).
 *
 * One device per nvidia_scf_pmu_<N> (one per Grace socket: 1 on GH200, 2 on GB200 /
 * Grace-Grace; never assume two). Each opens cmem_rd_data and cmem_wr_total_bytes from the PMU's
 * sysfs `events/` on its `cpumask` CPU, with the per-boot `type` read from sysfs, and reads them
 * scaled through arm_perf_util. Socket number = <N> from the PMU name, never the type number. */
#ifndef ARM_IMC_SCF_H
#define ARM_IMC_SCF_H

#include "arm_perf_util.h"

#define ARM_IMC_SCF_PMU_PREFIX "nvidia_scf_pmu_"
#define ARM_IMC_SCF_MAX_SOCKETS 16
/** Collect ticks between cheap re-scans while no memory PMU is attached (O17). */
#define ARM_IMC_SCF_RESCAN_TICKS 10

struct arm_imc_scf_dev {
  int socket; /* <N> of nvidia_scf_pmu_<N> */
  int cpu;    /* first CPU of that PMU's cpumask */
  int fd_rd;
  int fd_wr;
  struct arm_perf_prev prev_rd;
  struct arm_perf_prev prev_wr;
  unsigned long long life_rd_beats; /* scaled lifetime, 32-byte read beats */
  unsigned long long life_wr_bytes; /* scaled lifetime, bytes written */
};

struct arm_imc_scf {
  const char *devices_dir;
  arm_perf_open_fn open_fn;
  struct arm_imc_scf_dev dev[ARM_IMC_SCF_MAX_SOCKETS];
  int n;
  int ticks_since_scan;
  unsigned int fail_logged; /* bit per socket: open failure already logged */
};

/** How arm_aarch64_imc reads DRAM CAS. RESCAN: no memory PMU yet; the type stays enabled with
 * 0 devices and re-scans every ARM_IMC_SCF_RESCAN_TICKS ticks (late nvidia_cspmu binding). */
enum arm_imc_mode {
  ARM_IMC_MODE_RESCAN = 0,
  ARM_IMC_MODE_PERF,
  ARM_IMC_MODE_SCF,
};

/** SCF when any SCF device attached, else the perf dmc/imc devices, else RESCAN. */
int arm_imc_scf_begin_mode(int n_scf, int n_perf);

/** Resets @p s (no fds) for @p devices_dir; NULL open_fn uses arm_perf_open_cpu. */
void arm_imc_scf_init(struct arm_imc_scf *s, const char *devices_dir, arm_perf_open_fn open_fn);

/** Number of nvidia_scf_pmu_<N> entries under @p devices_dir (one opendir; 0 when none). */
int arm_imc_scf_count_pmus(const char *devices_dir);

/** Enumerates the SCF PMUs and opens both events per socket, ordered by <N>. A socket whose
 * events cannot be resolved or opened is skipped with one log line. @return devices attached. */
int arm_imc_scf_attach(struct arm_imc_scf *s);

/** Per collect tick while no device is attached: every ARM_IMC_SCF_RESCAN_TICKS ticks, re-scan
 * and attach when PMUs appeared. @return the number of attached devices. */
int arm_imc_scf_tick(struct arm_imc_scf *s);

/** Reads one device (scaled) and returns its lifetime CAS counts. @return 0, -1 on bad input. */
int arm_imc_scf_read(struct arm_imc_scf_dev *d, unsigned long long *cas_rd,
                     unsigned long long *cas_wr);

/** Closes every fd and forgets the devices. */
void arm_imc_scf_close(struct arm_imc_scf *s);

/** CMEM_RD_DATA counts 32-byte beats; arm_aarch64_imc CAS keys are 64-byte transfers. */
unsigned long long arm_imc_scf_rd_beats_to_cas(unsigned long long beats);

/** CMEM_WR_TOTAL_BYTES counts bytes. */
unsigned long long arm_imc_scf_wr_bytes_to_cas(unsigned long long bytes);

#endif
