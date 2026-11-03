/* arm_imc_scf — Grace SCF uncore helpers for arm_aarch64_imc (pure; no LIKWID). */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "arm_imc_scf.h"

#define ARM_IMC_SCF_MAX_SOCKETS 16

/* First CPU of a cpumask list such as "72" or "0-3,8"; -1 when unparsable. */
static int arm_imc_scf_first_cpu(const char *path)
{
  char buf[64];
  char *end = NULL;
  long cpu;
  FILE *f = fopen(path, "r");

  if (f == NULL)
    return -1;
  if (fgets(buf, sizeof(buf), f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);
  cpu = strtol(buf, &end, 10);
  if (end == buf || cpu < 0 || cpu > 1 << 20)
    return -1;
  return (int)cpu;
}

int arm_imc_scf_lead_cpus(const char *devices_dir, int *cpus, int cap)
{
  int lead[ARM_IMC_SCF_MAX_SOCKETS];
  int i;
  int n = 0;
  size_t plen = strlen(ARM_IMC_SCF_PMU_PREFIX);
  struct dirent *de;
  DIR *d;

  if (devices_dir == NULL || cpus == NULL || cap <= 0)
    return 0;
  d = opendir(devices_dir);
  if (d == NULL)
    return 0;
  for (i = 0; i < ARM_IMC_SCF_MAX_SOCKETS; i++)
    lead[i] = -1;
  while ((de = readdir(d)) != NULL) {
    char path[512];
    char *end = NULL;
    long sock;

    if (strncmp(de->d_name, ARM_IMC_SCF_PMU_PREFIX, plen) != 0)
      continue;
    sock = strtol(de->d_name + plen, &end, 10);
    if (end == de->d_name + plen || *end != '\0' || sock < 0 || sock >= ARM_IMC_SCF_MAX_SOCKETS)
      continue;
    if (snprintf(path, sizeof(path), "%s/%s/cpumask", devices_dir, de->d_name) >= (int)sizeof(path))
      continue;
    lead[sock] = arm_imc_scf_first_cpu(path);
  }
  closedir(d);
  for (i = 0; i < ARM_IMC_SCF_MAX_SOCKETS && n < cap; i++)
    if (lead[i] >= 0)
      cpus[n++] = lead[i];
  return n;
}

unsigned long long arm_imc_scf_rd_beats_to_cas(unsigned long long beats)
{
  return beats / 2ULL;
}

unsigned long long arm_imc_scf_wr_bytes_to_cas(unsigned long long bytes)
{
  return bytes / 64ULL;
}
