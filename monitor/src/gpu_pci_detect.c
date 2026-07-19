/* gpu_pci_detect — shared lspci GPU vendor heuristics for runtime and build probes. */
#include <string.h>

#include "gpu_pci_detect.h"

int gpu_pci_line_is_gpu_class(const char *line_lower)
{
  if (line_lower == NULL)
    return 0;
  return strstr(line_lower, "vga compatible controller") != NULL
      || strstr(line_lower, "3d controller") != NULL
      || strstr(line_lower, "display controller") != NULL
      || strstr(line_lower, "processing accelerators") != NULL
      || strstr(line_lower, "accelerator") != NULL;
}

int gpu_pci_line_nvidia_pci_id(const char *line_lower)
{
  if (line_lower == NULL || strstr(line_lower, "[10de:") == NULL)
    return 0;
  return strstr(line_lower, "[0300]") != NULL || strstr(line_lower, "[0301]") != NULL
      || strstr(line_lower, "[0302]") != NULL || strstr(line_lower, "[0680]") != NULL
      || strstr(line_lower, "[1202]") != NULL || strstr(line_lower, "3d controller") != NULL
      || strstr(line_lower, "vga compatible controller") != NULL
      || strstr(line_lower, "display controller") != NULL
      || strstr(line_lower, "processing accelerators") != NULL;
}

int gpu_pci_line_indicates_nvidia(const char *line_lower)
{
  if (line_lower == NULL)
    return 0;
  if (gpu_pci_line_is_gpu_class(line_lower) && strstr(line_lower, "nvidia") != NULL)
    return 1;
  return gpu_pci_line_nvidia_pci_id(line_lower);
}

int gpu_pci_line_indicates_amd(const char *line_lower)
{
  if (line_lower == NULL || !gpu_pci_line_is_gpu_class(line_lower))
    return 0;
  return strstr(line_lower, "advanced micro devices") != NULL
      || strstr(line_lower, " amd/ati ") != NULL;
}

int gpu_pci_line_indicates_intel_datacenter_gpu(const char *line_lower)
{
  int class_ok;

  if (line_lower == NULL)
    return 0;
  if (strstr(line_lower, "matrox") != NULL)
    return 0;
  class_ok = gpu_pci_line_is_gpu_class(line_lower)
      || strstr(line_lower, "[0380]") != NULL;
  if (!class_ok)
    return 0;
  return strstr(line_lower, "ponte vecchio") != NULL
      || strstr(line_lower, "data center gpu max") != NULL
      || strstr(line_lower, "[8086:0bd5]") != NULL;
}
