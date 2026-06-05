/*! \file amd64_pmu_core.c
 *  Shared AMD MSR PMU programming helpers for core and DF counters.
 */

#include "amd64_pmu_core.h"
#include "amd64_pmc.h"
#include "msr_io.h"
#include "trace.h"
#include <fcntl.h>
#include <stdint.h>
#include <unistd.h>

int amd64_pmu_msr_program_selects(char *cpu, uint64_t ctl0_msr,
          const uint64_t *events, int n_events)
{
  int rc = -1;
  int msr_fd = -1;
  int i;

  msr_fd = msr_open_cpu(cpu, O_RDWR);
  if (msr_fd < 0)
    goto out;

  for (i = 0; i < n_events; i++) {
    unsigned int addr =
        (unsigned int)(ctl0_msr + (uint64_t)i * 2u);

    TRACE("MSR %08X, event %016llX\n", addr,
          (unsigned long long)events[i]);
    if (msr_write_u64(msr_fd, addr, events[i]) < 0) {
      ERROR("cannot write event %016llX to MSR %08X for cpu `%s': %m\n",
            (unsigned long long)events[i], addr, cpu);
      goto out;
    }
  }

  rc = 0;

out:
  if (msr_fd >= 0)
    close(msr_fd);
  return rc;
}

int amd64_pmu_core_program_counters_with_hwcr(char *cpu,
                const uint64_t *events,
                int n_events)
{
  int rc = -1;
  int msr_fd = -1;
  int i;
  uint64_t hwcr = 0;

  msr_fd = msr_open_cpu(cpu, O_RDWR);
  if (msr_fd < 0)
    goto out;

  for (i = 0; i < n_events; i++) {
    unsigned int addr =
        (unsigned int)((uint64_t)MSR_PERF_CTL0 + (uint64_t)i * 2u);

    TRACE("MSR %08X, event %016llX\n", addr,
          (unsigned long long)events[i]);
    if (msr_write_u64(msr_fd, addr, events[i]) < 0) {
      ERROR("cannot write event %016llX to MSR %08X for cpu `%s': %m\n",
            (unsigned long long)events[i], addr, cpu);
      goto out;
    }
  }

  if (msr_read_u64(msr_fd, MSR_HW_CONFIG, &hwcr) < 0) {
    ERROR("cannot read HWCR before enabling instr retired ctr (MSR %08X) for cpu `%s': %m\n",
          (unsigned)MSR_HW_CONFIG, cpu);
    goto out;
  }
  hwcr |= (1ULL << 30);
  if (msr_write_u64(msr_fd, MSR_HW_CONFIG, hwcr) < 0) {
    ERROR("cannot enable instr retired ctr at MSR %08X for cpu `%s': %m\n",
          (unsigned)MSR_HW_CONFIG, cpu);
    goto out;
  }

  rc = 0;

out:
  if (msr_fd >= 0)
    close(msr_fd);
  return rc;
}
