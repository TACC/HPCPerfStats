/* Allowlisted GPU/CPU peak identity tables and nvidia-smi CSV parsers (no live GPU). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "roofline_hw_peak_identity.h"

static void trim_inplace(char *s)
{
  char *end;
  char *start = s;

  while (*start == ' ' || *start == '\t')
    start++;
  if (start != s)
    memmove(s, start, strlen(start) + 1);
  end = s + strlen(s);
  while (end > s && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\n' || end[-1] == '\r'))
    end--;
  *end = '\0';
}

/* Split line on commas into up to max_fields; returns field count. */
static int split_csv_fields(const char *line, char fields[][256], int max_fields)
{
  const char *p = line;
  int n = 0;

  if (line == NULL || fields == NULL || max_fields <= 0)
    return 0;
  while (n < max_fields && *p != '\0') {
    const char *comma = strchr(p, ',');
    size_t len;

    if (comma != NULL)
      len = (size_t)(comma - p);
    else
      len = strlen(p);
    if (len >= 256)
      len = 255;
    memcpy(fields[n], p, len);
    fields[n][len] = '\0';
    trim_inplace(fields[n]);
    n++;
    if (comma == NULL)
      break;
    p = comma + 1;
  }
  return n;
}

int roofline_nvidia_sm_count_from_name(const char *name)
{
  if (name == NULL)
    return 0;
  if (strstr(name, "GB200") != NULL || strstr(name, "B200") != NULL)
    return ROOFLINE_GB200_SM_COUNT;
  return 0;
}

double roofline_nvidia_hbm_bw_from_name(const char *name)
{
  if (name == NULL)
    return 0.0;
  if (strstr(name, "GB200") != NULL || strstr(name, "B200") != NULL)
    return ROOFLINE_GB200_HBM_BW_BYTES_PER_S;
  return 0.0;
}

double roofline_grace_dram_bw_from_cpu_part(unsigned int cpu_part)
{
  if (cpu_part == ROOFLINE_GRACE_CPU_PART)
    return ROOFLINE_GRACE_DRAM_BW_BYTES_PER_S;
  return 0.0;
}

double roofline_pcie_gen_lane_bytes_per_s(int gen)
{
  if (gen >= 6)
    return 7.877e9;
  if (gen >= 5)
    return 3.938e9;
  if (gen >= 4)
    return 1.969e9;
  if (gen >= 3)
    return 0.985e9;
  if (gen >= 2)
    return 0.500e9;
  if (gen >= 1)
    return 0.250e9;
  return 0.0;
}

double roofline_nvidia_fp64_ratio_from_cc(int major, int minor, const char *name)
{
  (void)name;
  if (major >= 9)
    return 0.5;
  if (major == 8)
    return (minor == 0) ? 0.5 : (1.0 / 64.0);
  if (major == 7)
    return (minor == 0) ? 0.5 : (1.0 / 32.0);
  if (major == 6)
    return (minor == 0) ? 0.5 : (1.0 / 32.0);
  return 1.0 / 32.0;
}

int roofline_nvidia_cuda_cores_per_sm(int major, int minor, const char *name)
{
  if (major >= 9)
    return 128;
  if (major == 8) {
    if (minor == 0)
      return 64;
    return 128;
  }
  if (major == 7)
    return 64;
  if (major == 6)
    return (minor == 0) ? 64 : 128;
  if (major == 5)
    return 128;
  if (major == 3)
    return 192;
  if (name != NULL && strstr(name, "Tesla K80") != NULL)
    return 192;
  return 128;
}

double roofline_nvidia_fp64_flops_from_sm(int sm_count, int cores_per_sm, double sm_mhz,
                                          double fp64_ratio)
{
  if (sm_count <= 0 || cores_per_sm <= 0 || sm_mhz <= 0.0 || fp64_ratio <= 0.0)
    return 0.0;
  return (double)sm_count * (double)cores_per_sm * (sm_mhz * 1.0e6) * 2.0 * fp64_ratio;
}

int roofline_parse_smi_flops_line(const char *line, char *name, size_t name_cap, double *sm_mhz,
                                  int *cc_major, int *cc_minor)
{
  char fields[4][256];
  int n;
  int maj = 0, min = 0;

  if (line == NULL || name == NULL || name_cap == 0 || sm_mhz == NULL || cc_major == NULL ||
      cc_minor == NULL)
    return -1;
  n = split_csv_fields(line, fields, 4);
  if (n < 3)
    return -1;
  snprintf(name, name_cap, "%s", fields[0]);
  *sm_mhz = strtod(fields[1], NULL);
  if (sscanf(fields[2], "%d.%d", &maj, &min) != 2)
    return -1;
  *cc_major = maj;
  *cc_minor = min;
  return (*sm_mhz > 0.0 && name[0] != '\0') ? 0 : -1;
}

int roofline_parse_smi_mem_pcie_line(const char *line, char *name, size_t name_cap,
                                     double *mem_total_mib, double *mem_clock_mhz, int *pcie_gen,
                                     int *pcie_width)
{
  char fields[5][256];
  int n;

  if (line == NULL || name == NULL || name_cap == 0 || mem_total_mib == NULL ||
      mem_clock_mhz == NULL || pcie_gen == NULL || pcie_width == NULL)
    return -1;
  n = split_csv_fields(line, fields, 5);
  if (n < 5)
    return -1;
  snprintf(name, name_cap, "%s", fields[0]);
  *mem_total_mib = strtod(fields[1], NULL);
  *mem_clock_mhz = strtod(fields[2], NULL);
  *pcie_gen = (int)strtol(fields[3], NULL, 10);
  *pcie_width = (int)strtol(fields[4], NULL, 10);
  return (name[0] != '\0' && *pcie_gen > 0 && *pcie_width > 0) ? 0 : -1;
}
