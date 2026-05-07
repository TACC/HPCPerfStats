#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
#include <unistd.h>
#include "stats.h"
#include "trace.h"

static void disable_type_if_present(const char *name)
{
  struct stats_type *type = stats_type_get(name);
  if (type != NULL)
    type->st_enabled = 0;
}

static void to_lower_ascii(char *s)
{
  while (*s != '\0') {
    *s = (char) tolower((unsigned char) *s);
    s++;
  }
}

/* lspci can omit the word "Infiniband" for some Mellanox lines; class 0207 is InfiniBand. */
static int infiniband_sysfs_has_devices(void)
{
  DIR *d;
  struct dirent *ent;

  d = opendir("/sys/class/infiniband");
  if (d == NULL)
    return 0;
  while ((ent = readdir(d)) != NULL) {
    if (ent->d_name[0] == '.')
      continue;
    closedir(d);
    return 1;
  }
  closedir(d);
  return 0;
}

/*
 * Superchips / GH systems sometimes omit "nvidia" in lspci wording or only expose PCI IDs.
 * Prefer conservative positives over disabling GPU telemetry when the driver is present.
 */
static int sysfs_proc_indicates_nvidia_gpu(void)
{
  if (access("/proc/driver/nvidia/version", R_OK) == 0)
    return 1;
  if (access("/dev/nvidia0", F_OK) == 0)
    return 1;
  return 0;
}

static int lspci_line_nvidia_pci_gpu_device(const char *line)
{
  if (strstr(line, "[10de:") == NULL)
    return 0;
  return strstr(line, "[0300]") != NULL || strstr(line, "[0301]") != NULL
      || strstr(line, "[0302]") != NULL || strstr(line, "[0680]") != NULL
      || strstr(line, "[1202]") != NULL || strstr(line, "3d controller") != NULL
      || strstr(line, "vga compatible controller") != NULL
      || strstr(line, "display controller") != NULL
      || strstr(line, "processing accelerators") != NULL;
}

void hwdetect_probe_optional_stack_presence(int *has_nvidia_gpu,
                                            int *has_amd_gpu,
                                            int *has_ib,
                                            int *has_opa)
{
  int nvidia = 0;
  int amd = 0;
  int ib = 0;
  int opa = 0;
  FILE *fp = popen("lspci -nn 2>/dev/null", "r");
  char line[1024];

  if (fp == NULL) {
    if (has_nvidia_gpu != NULL)
      *has_nvidia_gpu = sysfs_proc_indicates_nvidia_gpu();
    if (has_amd_gpu != NULL)
      *has_amd_gpu = 0;
    if (has_ib != NULL)
      *has_ib = infiniband_sysfs_has_devices();
    if (has_opa != NULL)
      *has_opa = 0;
    return;
  }

  while (fgets(line, sizeof(line), fp) != NULL) {
    to_lower_ascii(line);
    if (strstr(line, "vga compatible controller") != NULL ||
        strstr(line, "3d controller") != NULL ||
        strstr(line, "display controller") != NULL ||
        strstr(line, "processing accelerators") != NULL ||
        strstr(line, "accelerator") != NULL) {
      if (strstr(line, "nvidia") != NULL)
        nvidia = 1;
      if (strstr(line, "advanced micro devices") != NULL ||
          strstr(line, " amd/ati ") != NULL)
        amd = 1;
    }
    if (strstr(line, "infiniband") != NULL || strstr(line, "[0207]") != NULL)
      ib = 1;
    if (strstr(line, "omnipath") != NULL || strstr(line, "hfi") != NULL)
      opa = 1;
    if (!nvidia && lspci_line_nvidia_pci_gpu_device(line))
      nvidia = 1;
  }
  pclose(fp);

  if (!ib)
    ib = infiniband_sysfs_has_devices();

  if (!nvidia)
    nvidia = sysfs_proc_indicates_nvidia_gpu();

  if (has_nvidia_gpu != NULL)
    *has_nvidia_gpu = nvidia;
  if (has_amd_gpu != NULL)
    *has_amd_gpu = amd;
  if (has_ib != NULL)
    *has_ib = ib;
  if (has_opa != NULL)
    *has_opa = opa;
}

void auto_disable_optional_stats_by_lspci(void)
{
  int has_nvidia_gpu = 0;
  int has_amd_gpu = 0;
  int has_ib = 0;
  int has_opa = 0;

  hwdetect_probe_optional_stack_presence(&has_nvidia_gpu, &has_amd_gpu, &has_ib, &has_opa);

  if (!has_nvidia_gpu)
    disable_type_if_present("nvidia_gpu");
  if (!has_amd_gpu)
    disable_type_if_present("amd_gpu");
  if (!has_ib) {
    disable_type_if_present("ib");
    disable_type_if_present("ib_ext");
    disable_type_if_present("ib_sw");
  }
  if (!has_opa)
    disable_type_if_present("opa");
}
