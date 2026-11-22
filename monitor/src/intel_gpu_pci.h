/* intel_gpu_pci - count Intel Data Center GPU Max (Ponte Vecchio) PCI functions in sysfs. */
#ifndef INTEL_GPU_PCI_H
#define INTEL_GPU_PCI_H

/*! 1 when a PCI function (sysfs `vendor`, `device`, 24-bit `class`) is a Data Center GPU Max:
 *  vendor 0x8086, base class display (0x03) or processing accelerator (0x12), and a Ponte
 *  Vecchio device id. Mirrored by scripts/lib/host_live_probes.py probe_intel_gpu_devices. */
int intel_gpu_pci_is_max_series(unsigned int vendor, unsigned int device, unsigned int pci_class);

/*! Number of Data Center GPU Max functions under `<sysfs_root>/bus/pci/devices`, at most `max`;
 *  0 when the directory cannot be read. */
int intel_gpu_pci_count(const char *sysfs_root, int max);

#endif
