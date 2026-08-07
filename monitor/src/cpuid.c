/* Currently only used for intel chips after Nehalem - x2APIC chips */
/* nhm, wtm, snb, ivb, hsw are classified correctly */
/* Will autoset AMD to 4 counters */
#include <stdio.h>
#include <unistd.h>
#include <stdint.h>
#include <fcntl.h>
#include <string.h>
#include "trace.h"
#include "path_open_fail_once.h"
#include "cpuid.h"
#include "intel_cpuid_match.h"
#include "amd_cpuid_match.h"

#if defined(__i386__) || defined(__x86_64__)

#define cpuid(func, ax, bx, cx, dx)                                                                \
  __asm__ __volatile__("cpuid" : "=a"(ax), "=b"(bx), "=c"(cx), "=d"(dx) : "a"(func))

processor_t signature(int *n_pmcs)
{
  uint32_t eax = 0, ebx = 0, ecx = 0, edx = 0;
  char vendor[13];
  processor_t rc = (processor_t)-1;

  if (n_pmcs == NULL)
    return rc;
  *n_pmcs = 0;
  cpuid(0, eax, ebx, ecx, edx);
  snprintf(vendor, sizeof(vendor), "%c%c%c%c%c%c%c%c%c%c%c%c", ebx & 0xff, (ebx >> 8) & 0xff,
           (ebx >> 16) & 0xff, (ebx >> 24) & 0xff, edx & 0xff, (edx >> 8) & 0xff,
           (edx >> 16) & 0xff, (edx >> 24) & 0xff, ecx & 0xff, (ecx >> 8) & 0xff,
           (ecx >> 16) & 0xff, (ecx >> 24) & 0xff);
  TRACE("vendor %s\n", vendor);

  cpuid(1, eax, ebx, ecx, edx);
  char sig[16];
  int model = (eax & 0x0FF) >> 4;
  int extended_model = (eax & 0xF0000) >> 12;
  int family_code = (eax & 0xF00) >> 8;
  int extended_family_code = (eax & 0xFF00000) >> 16;
  int stepping = (int)(eax & 0xF);
  snprintf(sig, sizeof(sig), "%02x_%x", extended_family_code | family_code, extended_model | model);
  TRACE("sig %s stepping %d\n", sig, stepping);

  /* Determine Processor Family and Model */
  if (strncmp(vendor, "GenuineIntel", 12) == 0) {
    cpuid(0x0A, eax, ebx, ecx, edx);
    *n_pmcs = (eax >> 8) & 0xFF;
    TRACE("Number of PMCs = %d\n", *n_pmcs);

    rc = intel_cpuid_sig_to_processor(vendor, sig, stepping);
    if (rc != (processor_t)-1) {
      TRACE("Intel processor sig %s stepping %d -> %d\n", sig, stepping, (int)rc);
      return rc;
    }
  } else if (strncmp(vendor, "AuthenticAMD", 12) == 0) {
    rc = amd_cpuid_sig_to_processor(vendor, sig);
    if (rc != (processor_t)-1) {
      *n_pmcs = 6;
      TRACE("AMD EPYC processor sig %s -> %d (n_pmcs=6)\n", sig, (int)rc);
      return rc;
    }
  }

  return rc;
}

int cpuid_read_cpu_topology(char *cpu, int *pkg_id, int *core_id, int *smt_id, int *nr_core)
{
  int i;
  char cpuid_path[80];
  int cpuid_fd = -1;
  uint32_t buf[4];
  int rc = 0;
  int max_leaf = 0;
  unsigned int x2APIC_ID = 0;
  int SMT_Mask_Width = -1, SMT_Select_Mask = -1;
  int CorePlus_Mask_Width = 0, CoreOnly_Select_Mask = 0;
  int Pkg_Select_Mask = 0;
  int nr_smt = 0;

  /* Open /dev/cpuid/CPU/cpuid. */
  snprintf(cpuid_path, sizeof(cpuid_path), "/dev/cpu/%s/cpuid", cpu);
  if (path_open_is_skipped(cpuid_path))
    goto out;
  cpuid_fd = open(cpuid_path, O_RDONLY);

  if (cpuid_fd < 0) {
    path_open_record_failure_once(cpuid_path);
    goto out;
  }

  /* Do cpuid 0 to get max leaf. */
  if (pread(cpuid_fd, buf, sizeof(buf), 0x0) < 0) {
    ERROR("cannot read cpu vendor through `%s': %m\n", cpuid_path);
    goto out;
  }

  // Get cpuid_level
  max_leaf = buf[0];
  if (max_leaf < 0xB)
    goto out;

  /* Do cpuid 0xB to get cpu APIC_ID. */
  if (pread(cpuid_fd, buf, sizeof(buf), 0xB) < 0) {
    ERROR("cannot read x2APIC ID through `%s': %m\n", cpuid_path);
    goto out;
  }
  x2APIC_ID = buf[3] & 0xFFFFFFFF;
  TRACE("APIC ID %d\n", x2APIC_ID);

  // Test for x2APIC
  if (buf[1] != 0) {
    for (i = 0; i <= max_leaf; i++) {
      /* Upper 32 bits gets level and lower 32 gets leaf */
      if (pread(cpuid_fd, buf, sizeof(buf), i * 0x100000000 | 0xB) < 0) {
        ERROR("could not read cpuid level %d through `%s': %m\n", i, cpuid_path);
        goto out;
      }
      TRACE("%s %d %d %X %d\n", cpuid_path, buf[0], buf[1], buf[2], buf[3]);

      /* Number of logical processors at this level, break if 0 */
      if ((buf[1] & 0xFFFF) == 0)
        break;

      /* SMT level type from EC[16:8] = 1 */
      if (((buf[2] >> 8) & 0xFF) == 1) {
        nr_smt = buf[1];
        SMT_Mask_Width = buf[0] & 0xF;
        SMT_Select_Mask = ~((-1) << SMT_Mask_Width);
        *smt_id = x2APIC_ID & SMT_Select_Mask;
      }
      /* Core level type from EC[16:8] = 2 */
      else if (((buf[2] >> 8) & 0xFF) == 2) {
        *nr_core = buf[1] / nr_smt;
        CorePlus_Mask_Width = buf[0] & 0xF;
        CoreOnly_Select_Mask = ~((-1) << CorePlus_Mask_Width) ^ SMT_Select_Mask;
        *core_id = (x2APIC_ID & CoreOnly_Select_Mask) >> SMT_Mask_Width;
        Pkg_Select_Mask = (-1) << CorePlus_Mask_Width;
        *pkg_id = (x2APIC_ID & Pkg_Select_Mask) >> CorePlus_Mask_Width;
        rc = 1;
        break;
      }
    }
  }
  TRACE("Number of threads/physical cores %d/%d\n", nr_smt, *nr_core);
  //printf("Number of threads/physical cores %d/%d\n", nr_smt, *nr_core);
  TRACE("Pkg_ID Core_ID SMT_ID %d %d %d\n", *pkg_id, *core_id, *smt_id);
  //printf("Pkg_ID Core_ID SMT_ID %d %d %d\n", *pkg_id, *core_id, *smt_id);
out:
  if (cpuid_fd >= 0)
    close(cpuid_fd);

  return rc;
}

#else /* !(__i386__ || __x86_64__): no CPUID instruction (e.g. AArch64 with nvc++/gcc) */

processor_t signature(int *n_pmcs)
{
  if (n_pmcs != NULL)
    *n_pmcs = 0;
  TRACE("cpuid: non-x86 host; processor signature unavailable\n");
  return (processor_t)-1;
}

int cpuid_read_cpu_topology(char *cpu, int *pkg_id, int *core_id, int *smt_id, int *nr_core)
{
  (void)cpu;
  if (pkg_id != NULL)
    *pkg_id = 0;
  if (core_id != NULL)
    *core_id = 0;
  if (smt_id != NULL)
    *smt_id = 0;
  if (nr_core != NULL)
    *nr_core = 0;
  return 0;
}

#endif
