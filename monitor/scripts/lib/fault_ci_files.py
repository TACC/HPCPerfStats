"""Source files whose stats_set_missing* failure sites make check forces.

Each file is linked into a fault-coverage target of tests/Makefile.am (test_fault_inject_*:
the matrix / hw drivers check stats_sentinel_sites_report, the others link test_site_coverage.c),
and those targets FAIL when any registered site in the binary never fired. A live fault-injection
run therefore treats a never-fired site in one of these files as covered by CI.
tests/test_fault_ci_files.sh keeps this set equal to the sources the targets link.
"""
from __future__ import annotations

CI_COVERED_FILES = frozenset(
    {
        # test_fault_inject_matrix
        "collect.c", "cpu.c", "ps.c", "vfs.c", "osc.c", "mdc.c",
        # test_fault_inject_hw
        "ib_family.c", "arm_power_socket.c", "nvidia_gpu.c",
        # test_fault_inject_fs
        "proc.c", "net.c", "tmpfs.c", "beegfs.c", "llite.c", "amd_gpu.c",
        # test_fault_inject_cpu (each backend compiles its own site)
        "cpu_counter_metrics.c",
        # test_fault_inject_x86_uncore
        "x86_uncore_perf.c",
        # coverage twins of the collector sentinel tests
        "opa_sysfs.c", "intel_gpu.c", "intel_gpu_xpumcli.c", "cpu_counter_metrics_x86_publish.c",
        "intel_rapl.c", "amd64_rapl.c", "cpu_counter_metrics_arm_publish.c", "host_mem_job.c",
    }
)
