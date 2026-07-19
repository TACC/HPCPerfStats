#ifndef GPU_PCI_DETECT_H_
#define GPU_PCI_DETECT_H_

/* Shared NVIDIA/AMD/Intel DC GPU PCI line heuristics (lowercase lspci -nn lines).
 * Used by runtime hwdetect and mirrored in scripts/gpu_lspci_detect.awk for
 * configure / build_static_bundle probes — keep in sync. */

int gpu_pci_line_is_gpu_class(const char *line_lower);
int gpu_pci_line_nvidia_pci_id(const char *line_lower);
int gpu_pci_line_indicates_nvidia(const char *line_lower);
int gpu_pci_line_indicates_amd(const char *line_lower);
/* Stampede3 Ponte Vecchio / Data Center GPU Max — not iGPU / Matrox. */
int gpu_pci_line_indicates_intel_datacenter_gpu(const char *line_lower);

#endif
