/* intel_gpu_pci - count Intel Data Center GPU Max (Ponte Vecchio) PCI functions in sysfs. */
#include <dirent.h>
#include <stdio.h>

#include "intel_gpu_pci.h"

/* Vendor 8086 device ids named "Ponte Vecchio" / "Data Center GPU Max" in the PCI ID database
 * (hwdata pci.ids 2026.02.16); Stampede3 PVC nodes carry 0x0bd5 (Max 1550). */
static const unsigned int intel_gpu_pci_max_ids[] = {
    0x0b69, 0x0b6e, 0x0bd0, 0x0bd4, 0x0bd5, 0x0bd6, 0x0bd7, 0x0bd8, 0x0bd9, 0x0bda, 0x0bdb,
};

int intel_gpu_pci_is_max_series(unsigned int vendor, unsigned int device, unsigned int pci_class)
{
  unsigned int base = pci_class >> 16;
  size_t i;

  if (vendor != 0x8086 || (base != 0x03 && base != 0x12))
    return 0;
  for (i = 0; i < sizeof(intel_gpu_pci_max_ids) / sizeof(intel_gpu_pci_max_ids[0]); i++)
    if (device == intel_gpu_pci_max_ids[i])
      return 1;
  return 0;
}

/* Parse one hex sysfs attribute (`0x8086`); -1 when unreadable. */
static int intel_gpu_pci_read_hex(const char *dir, const char *name, const char *attr,
                                  unsigned int *out)
{
  char path[512];
  FILE *f;
  int n;

  n = snprintf(path, sizeof(path), "%s/%s/%s", dir, name, attr);
  if (n < 0 || (size_t)n >= sizeof(path))
    return -1;
  f = fopen(path, "r");
  if (f == NULL)
    return -1;
  n = fscanf(f, "%x", out);
  fclose(f);
  return n == 1 ? 0 : -1;
}

int intel_gpu_pci_count(const char *sysfs_root, int max)
{
  char dir[512];
  DIR *d;
  struct dirent *e;
  int count = 0;
  int n;

  if (sysfs_root == NULL)
    return 0;
  n = snprintf(dir, sizeof(dir), "%s/bus/pci/devices", sysfs_root);
  if (n < 0 || (size_t)n >= sizeof(dir))
    return 0;
  d = opendir(dir);
  if (d == NULL)
    return 0;
  while (count < max && (e = readdir(d)) != NULL) {
    unsigned int vendor;
    unsigned int device;
    unsigned int pci_class;

    if (e->d_name[0] == '.')
      continue;
    if (intel_gpu_pci_read_hex(dir, e->d_name, "vendor", &vendor) == 0 &&
        intel_gpu_pci_read_hex(dir, e->d_name, "device", &device) == 0 &&
        intel_gpu_pci_read_hex(dir, e->d_name, "class", &pci_class) == 0 &&
        intel_gpu_pci_is_max_series(vendor, device, pci_class))
      count++;
  }
  closedir(d);
  return count;
}
