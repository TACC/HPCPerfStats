# Shared NVIDIA/AMD GPU PCI line heuristics for configure and build_static_bundle.
# Must stay aligned with src/gpu_pci_detect.c (see tests/test_gpu_lspci_detect_parity.sh).

function is_gpu_class(l,    n) {
  n = tolower(l)
  if (index(n, "vga compatible controller")) return 1
  if (index(n, "3d controller")) return 1
  if (index(n, "display controller")) return 1
  if (index(n, "processing accelerators")) return 1
  if (index(n, "accelerator")) return 1
  return 0
}

function nvidia_pci_id(l,    n) {
  n = tolower(l)
  if (index(n, "[10de:") == 0) return 0
  if (index(n, "[0300]")) return 1
  if (index(n, "[0301]")) return 1
  if (index(n, "[0302]")) return 1
  if (index(n, "[0680]")) return 1
  if (index(n, "[1202]")) return 1
  if (index(n, "3d controller")) return 1
  if (index(n, "vga compatible controller")) return 1
  if (index(n, "display controller")) return 1
  if (index(n, "processing accelerators")) return 1
  return 0
}

function line_nvidia(l,    n) {
  n = tolower(l)
  if (is_gpu_class(n) && index(n, "nvidia")) return 1
  return nvidia_pci_id(l)
}

function line_amd(l,    n) {
  n = tolower(l)
  if (!is_gpu_class(n)) return 0
  if (index(n, "advanced micro devices")) return 1
  if (index(n, " amd/ati ")) return 1
  return 0
}

BEGIN {
  found_nvidia = 0
  found_amd = 0
}

{
  if (line_nvidia($0)) found_nvidia = 1
  if (line_amd($0)) found_amd = 1
}

END {
  if (vendor == "nvidia") exit(found_nvidia ? 0 : 1)
  if (vendor == "amd") exit(found_amd ? 0 : 1)
  exit 1
}
