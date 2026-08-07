# Monitor unit tests (see tests/README.md). Sources under $(top_srcdir)/src/ are
# production code linked into small check_PROGRAMS drivers.
AUTOMAKE_OPTIONS = subdir-objects

STATS_DIR_PATH = /var/log/hpcperfstats
STATS_LOCK_PATH = /var/lock/hpcperfstats
JOBID_FILE_PATH = /var/run/stats_jobid

# Aligned with src/Makefile.am (RabbitMQ timeout feature flags).
MONITOR_AMQP_TIMEOUT_CPPFLAGS = \
	-DHAVE_AMQP_SET_RPC_TIMEOUT=1 \
	-DHAVE_AMQP_SET_HANDSHAKE_TIMEOUT=1

# Keep -D flags aligned with src/Makefile.am hpcperfstatsd_CPPFLAGS (include path
# is only -I$(top_srcdir)/src here).
monitor_unit_cppflags = \
	-D_GNU_SOURCE \
	-DSTATS_PROGRAM=\"@PACKAGE@\" \
	-DSTATS_VERSION=\"@PACKAGE_VERSION@\" \
	-DSTATS_DIR_PATH=\"$(STATS_DIR_PATH)\" \
	-DSTATS_LOCK_PATH=\"$(STATS_LOCK_PATH)\" \
	-DJOBID_FILE_PATH=\"$(JOBID_FILE_PATH)\" \
	@CPU_COUNTER_BACKEND_CPPFLAG@ \
	@MONITOR_FEATURE_CPPFLAGS@ \
	@METRIC_PROFILER_CPPFLAG@ \
	@METRIC_PROFILER_BACKEND_CPPFLAG@

if DEBUG
monitor_unit_cppflags += -DDEBUG
endif

if LIKWID
monitor_unit_cppflags += -DHAVE_LIKWID @MONITOR_ARCH_CPPFLAG@
endif

if RABBITMQ
monitor_unit_cppflags += -DRABBITMQ $(MONITOR_AMQP_TIMEOUT_CPPFLAGS)
endif

if MONITOR_USE_DCGM_VENDOR_INCLUDES
monitor_unit_cppflags += -I$(top_srcdir)/third_party/nvidia-dcgm
endif

AM_CPPFLAGS = -I$(top_srcdir)/src $(monitor_unit_cppflags)

check_PROGRAMS = test_cpuid_signature test_amd_cpuid_match test_amd_df_likwid_profiles test_amd_x86_uncore_df_schema test_likwid_rapl_scale test_likwid_rapl_support test_amd64_rapl_likwid_ready test_likwid_pmc_force_env test_likwid_pmc_schema_map test_likwid_arch_map_spr test_likwid_uncore_profiles test_likwid_uncore_adapter test_host_edac_mem_topology test_intel_skx_imc_schema test_intel_icx_imc_schema test_intel_spr_imc_schema test_intel_emr_imc_schema test_intel_gnr_imc_schema test_intel_srf_imc_schema test_string1 test_stats_buffer_data_append test_stats_buffer_uts test_stats_buffer_rows test_path_collect test_path_open_fail_once test_path_read test_sys_iter test_procfile_parse test_monitor_log test_monitor_release_logging test_monitor_options_kv test_pscanf test_schema_parse test_stats_file_format test_stats_file_format_extra test_stats_text_format test_gpu_schema_contract test_cpu_counter_metrics_schema test_cpu_counter_papi_map test_cpu_counter_papi_util test_roofline_hw_peak_schema test_roofline_hw_peak_changeover test_roofline_detect_fixture test_nfs_schema_subset test_ib_schema_contract test_host_schema_contract test_lustre_schema_contract test_lustre_proc_stats test_beegfs_schema_contract test_beegfs_ctl_parse test_opa_schema_contract test_opa_sysfs_map test_opa_mad_backoff test_ib_hca_is_opa_hfi test_opa_lspci_match test_lnet_schema_contract test_arm_aarch64_imc_schema test_metric_profiler test_hwdetect_policy test_hwdetect_lspci test_gpu_pci_detect test_intel_gpu_schema_contract test_intel_gpu_publish test_xpum_gpu_dyn test_dcgm_gpu_dyn test_dcgm_session test_ib_mad_dyn test_opa_mad_dyn test_ib_port_state test_ib_port_collectible test_ib_foreach_hca_port test_ib_family test_stats_runtime_type_controls test_stats_runtime_likwid_begin_order test_stats_runtime_collect test_collect_tier_table test_collect_phase test_collect_tier_gate test_stats_set_tier_gate test_dict test_host_key_alias test_proc_status_order test_schema_emit test_procfile_kv test_collect_str test_nvidia_gpu_estimate test_nvidia_gpu_dcgm_field test_daemonize
if INFINIBAND
check_PROGRAMS += test_ib_mad_backoff test_ib_mad_decode test_ib_mad_sw_swap
endif
if CPU_BACKEND_DCGM
check_PROGRAMS += test_dcgm_pkg_uniq
endif
# Pure DCGM util/publish math must run on LIKWID hosts too (blank watts, util@clock=0).
check_PROGRAMS += test_cpu_counter_dcgm_util test_cpu_counter_dcgm_publish \
	test_cpu_counter_dcgm_publish_papi
if RABBITMQ
check_PROGRAMS += test_monitor_cli test_ring_buffer test_stats_buffer_collect test_stats_buffer_debug_shm test_debug_shm_emit_golden test_debug_shm_schema_mirror test_monitor_timing
endif

TESTS = $(check_PROGRAMS)

# Run from source (not generated): avoids Automake distdir-am needing a built .sh on some versions.
check-local:
	$(SHELL) '$(srcdir)/test_monitor_configure_help.sh.in'
	$(SHELL) '$(srcdir)/test_static_bundle_configure_flags.sh'
	$(SHELL) '$(srcdir)/test_prepare_rpmbuild_stampede3.sh'
	$(SHELL) '$(srcdir)/test_gpu_lspci_detect_parity.sh'
	$(SHELL) '$(srcdir)/test_monitor_configure_amd_auto.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_monitor_configure_intel_gpu_auto.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_make_dist_extra_dist_prereqs.sh'
	$(SHELL) '$(srcdir)/test_valgrind_cpp_linter_scripts.sh'
	$(SHELL) '$(srcdir)/test_prepare_monitor_tree_clean.sh'
	$(SHELL) '$(srcdir)/test_hpc_debug_build_configure.sh'
	$(SHELL) '$(srcdir)/test_rpm_debug_shm_verify.sh'
	$(SHELL) '$(srcdir)/test_monitor_release_logging_contract.sh'
	$(SHELL) '$(srcdir)/test_monitor_arch_auto_amd.sh'
	$(SHELL) '$(srcdir)/test_shm_validation_lib.sh'
	$(SHELL) '$(srcdir)/test_validate_stampede3_profile.sh'
	$(SHELL) '$(srcdir)/test_net_debug_trace_format.sh'
	$(SHELL) '$(srcdir)/test_papi_hybrid_configure.sh'
	$(SHELL) '$(srcdir)/test_configure_rabbitmq_static_probe.sh'
	$(SHELL) '$(srcdir)/test_likwid_read_group_counters.sh'
	$(SHELL) '$(srcdir)/test_amd_legacy_removed.sh'
	$(SHELL) '$(srcdir)/test_intel_legacy_pmc_removed.sh'
	$(SHELL) '$(srcdir)/test_lshw_stub.sh'
	$(SHELL) '$(srcdir)/test_service_unit_lshw.sh'
	python3 '$(top_srcdir)/scripts/check_emitted_variable_names.py'
	$(SHELL) '$(top_srcdir)/scripts/check_unsafe_c_patterns.sh'
	@$(SHELL) '$(srcdir)/test_shm_message_correctness.sh' '$(top_builddir)' || test $$? -eq 77
if RABBITMQ
	@if test "x$${RUN_RMQ_INTEGRATION:-0}" = "x1"; then \
	  $(MAKE) check-rabbitmq-integration; \
	fi
endif

EXTRA_DIST = test_monitor_configure_help.sh.in test_static_bundle_configure_flags.sh test_prepare_rpmbuild_stampede3.sh test_ib_mad_dyn.c test_opa_mad_dyn.c test_gpu_lspci_detect_parity.sh test_monitor_configure_amd_auto.sh test_monitor_configure_intel_gpu_auto.sh fixtures/gpu_lspci_lines.tsv fixtures/opa_lspci_lines.tsv fixtures/beegfs/clientstats_storage.txt fixtures/beegfs/clientstats_meta.txt fixtures/beegfs/config_snippet.txt fixtures/stampede3_lspci_profiles/README.md fixtures/stampede3_lspci_profiles/skx fixtures/stampede3_lspci_profiles/icx fixtures/stampede3_lspci_profiles/spr fixtures/stampede3_lspci_profiles/h100 fixtures/stampede3_lspci_profiles/pvc fixtures/stampede3_lspci_profiles/amd-rtx fixtures/vista_lspci_profiles/README.md fixtures/vista_lspci_profiles/gg fixtures/vista_lspci_profiles/gh test_validate_stampede3_profile.sh test_make_dist_extra_dist_prereqs.sh test_valgrind_cpp_linter_scripts.sh test_prepare_monitor_tree_clean.sh test_hpc_debug_build_configure.sh test_rpm_debug_shm_verify.sh test_monitor_release_logging_contract.sh test_monitor_arch_auto_amd.sh test_net_debug_trace_format.sh test_papi_hybrid_configure.sh test_configure_rabbitmq_static_probe.sh test_likwid_read_group_counters.sh test_amd_legacy_removed.sh test_intel_legacy_pmc_removed.sh test_lshw_stub.sh test_service_unit_lshw.sh test_dcgm_session.c run_tests.sh README.md test_ring_buffer.c test_ring_buffer_stubs.c test_stats_buffer_collect.c test_stats_buffer_collect_stubs.c test_stats_buffer_collect_stubs.h test_debug_shm_emit_fixture.c test_debug_shm_emit_fixture.h test_debug_shm_emit_validate.c test_debug_shm_emit_validate.h test_debug_shm_emit_golden.c test_debug_shm_schema_mirror.c test_shm_message_correctness.sh test_shm_validation_lib.sh test_shm_validation_lib.py test_host_edac_mem_topology.c expected/debug_shm_fast.txt expected/debug_shm_full.txt expected/synthetic_fixture/schema expected/synthetic_fixture/fast expected/synthetic_fixture/full expected/synthetic_fixture/monitor-build-capabilities.json expected/synthetic_fixture/expectations_synthetic_debug_tier1.json test_stats_buffer_rows.c test_stats_runtime_collect.c test_ib_family.c test_ib_mad_backoff.c test_ib_mad_decode.c test_ib_mad_stubs.c test_ib_mad_sw_swap.c test_nvidia_gpu_estimate.c test_roofline_detect_fixture.c test_daemonize.c test_path_open_fail_once.c test_monitor_timing.c test_stats_text_format.c test_arm_aarch64_imc_schema.c test_rabbitmq_integration.sh rmq_integration_validate.py requirements-rabbitmq-integration.txt requirements-cpp-linter.txt scripts/bootstrap_local_rabbitmq.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.allowlist $(top_srcdir)/scripts/check_emitted_variable_names.py $(top_srcdir)/scripts/emit_build_capabilities.py $(top_srcdir)/scripts/build_message_expectations.py $(top_srcdir)/scripts/validate_shm_messages.py $(top_srcdir)/scripts/lib/message_parse.py $(top_srcdir)/scripts/lib/row_validate.py $(top_srcdir)/scripts/lib/payload_parse.py $(top_srcdir)/scripts/lib/device_validate.py $(top_srcdir)/scripts/lib/listend_contract.py $(top_srcdir)/scripts/lib/host_live_probes.py $(top_srcdir)/scripts/lib/value_plausibility.py $(top_srcdir)/scripts/lib/live_spot_check.py $(top_srcdir)/scripts/lib/golden_diff.py $(top_srcdir)/scripts/lib/tacc_system_profiles.py $(top_srcdir)/scripts/validate_stampede3_profile.sh $(top_srcdir)/scripts/lib/__init__.py

if RABBITMQ
.PHONY: check-rabbitmq-integration
check-rabbitmq-integration:
	$(SHELL) '$(srcdir)/test_rabbitmq_integration.sh' '$(top_builddir)'
endif

test_likwid_rapl_scale_SOURCES = \
	test_likwid_rapl_scale.c \
	$(top_srcdir)/src/likwid_rapl.c \
	$(top_srcdir)/src/amd_processor.c \
	$(top_srcdir)/src/monitor_log.c

test_likwid_rapl_support_SOURCES = \
	test_likwid_rapl_support.c \
	$(top_srcdir)/src/likwid_rapl.c \
	$(top_srcdir)/src/amd_processor.c \
	$(top_srcdir)/src/monitor_log.c


test_amd64_rapl_likwid_ready_SOURCES = \
	test_amd64_rapl_likwid_ready.c \
	$(top_srcdir)/src/amd64_rapl.c \
	$(top_srcdir)/src/likwid_rapl.c \
	$(top_srcdir)/src/amd_processor.c \
	$(top_srcdir)/src/monitor_log.c

test_likwid_pmc_force_env_SOURCES = \
	test_likwid_pmc_force_env.c \
	$(top_srcdir)/src/likwid_pmc_force_env.c

test_likwid_pmc_schema_map_SOURCES = \
	test_likwid_pmc_schema_map.c \
	$(top_srcdir)/src/likwid_pmc_schema_map.c

test_likwid_arch_map_spr_SOURCES = \
	test_likwid_arch_map_spr.c \
	$(top_srcdir)/src/likwid_arch_map.c

test_cpuid_signature_SOURCES = \
	test_cpuid_signature.c \
	$(top_srcdir)/src/intel_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c

test_amd_cpuid_match_SOURCES = \
	test_amd_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c

test_amd_df_likwid_profiles_SOURCES = \
	test_amd_df_likwid_profiles.c \
	$(top_srcdir)/src/likwid_uncore_profiles.c \
	$(top_srcdir)/src/intel_processor.c \
	$(top_srcdir)/src/amd_processor.c

test_amd_x86_uncore_df_schema_SOURCES = test_amd_x86_uncore_df_schema.c

test_likwid_uncore_profiles_SOURCES = \
	test_likwid_uncore_profiles.c \
	$(top_srcdir)/src/likwid_uncore_profiles.c \
	$(top_srcdir)/src/intel_processor.c \
	$(top_srcdir)/src/amd_processor.c

test_likwid_uncore_adapter_SOURCES = \
	test_likwid_uncore_adapter.c \
	test_stats_stub.c \
	$(top_srcdir)/src/likwid_uncore_adapter.c \
	$(top_srcdir)/src/likwid_uncore_profiles.c \
	$(top_srcdir)/src/likwid_result_convert.c \
	$(top_srcdir)/src/host_edac_mem_topology.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/intel_processor.c \
	$(top_srcdir)/src/amd_processor.c

test_host_edac_mem_topology_SOURCES = \
	test_host_edac_mem_topology.c \
	$(top_srcdir)/src/host_edac_mem_topology.c

test_intel_skx_imc_schema_SOURCES = test_intel_skx_imc_schema.c
test_intel_icx_imc_schema_SOURCES = test_intel_icx_imc_schema.c
test_intel_spr_imc_schema_SOURCES = test_intel_spr_imc_schema.c
test_intel_emr_imc_schema_SOURCES = test_intel_emr_imc_schema.c
test_intel_gnr_imc_schema_SOURCES = test_intel_gnr_imc_schema.c
test_intel_srf_imc_schema_SOURCES = test_intel_srf_imc_schema.c

test_dict_SOURCES = \
	test_dict.c \
	$(top_srcdir)/src/dict.c

test_host_key_alias_SOURCES = \
	test_host_key_alias.c \
	test_stats_stub.c \
	$(top_srcdir)/src/host_key_alias.c

test_proc_status_order_SOURCES = \
	test_proc_status_order.c \
	test_stats_stub.c \
	$(top_srcdir)/src/proc_status.c \
	$(top_srcdir)/src/host_key_alias.c

test_schema_emit_SOURCES = \
	test_schema_emit.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_procfile_kv_SOURCES = \
	test_procfile_kv.c \
	test_stats_stub.c

test_collect_str_SOURCES = \
	test_collect_str.c \
	test_stats_stub.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_cpu_counter_dcgm_util_SOURCES = \
	test_cpu_counter_dcgm_util.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c
test_cpu_counter_dcgm_util_CPPFLAGS = $(AM_CPPFLAGS) -DMONITOR_CPU_BACKEND_DCGM

test_cpu_counter_dcgm_publish_SOURCES = \
	test_cpu_counter_dcgm_publish.c \
	test_stats_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_publish.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c
# Force DCGM backend define for pure publish math; no -ldcgm / vendor headers required.
test_cpu_counter_dcgm_publish_CPPFLAGS = $(AM_CPPFLAGS) -DMONITOR_CPU_BACKEND_DCGM

# Same sources with hybrid PAPI gate — locks util×freq cycle fail-soft under MONITOR_CPU_PAPI_FLOPS.
test_cpu_counter_dcgm_publish_papi_SOURCES = \
	test_cpu_counter_dcgm_publish.c \
	test_stats_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_publish.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c
test_cpu_counter_dcgm_publish_papi_CPPFLAGS = $(AM_CPPFLAGS) -DMONITOR_CPU_BACKEND_DCGM \
	-DMONITOR_CPU_PAPI_FLOPS
test_cpu_counter_dcgm_publish_papi_CFLAGS = $(AM_CFLAGS)

test_cpu_counter_papi_map_SOURCES = \
	test_cpu_counter_papi_map.c \
	test_stats_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_papi_map.c

test_cpu_counter_papi_util_SOURCES = \
	test_cpu_counter_papi_util.c \
	$(top_srcdir)/src/cpu_counter_metrics_papi_util.c

test_string1_SOURCES = test_string1.c

test_stats_buffer_data_append_SOURCES = \
	test_stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_buffer_data_append.c

test_stats_buffer_uts_SOURCES = \
	test_stats_buffer_uts.c \
	$(top_srcdir)/src/stats_buffer_uts.c

test_stats_buffer_rows_SOURCES = \
	test_stats_buffer_rows.c \
	test_stats_stub.c \
	$(top_srcdir)/src/stats_buffer_rows.c \
	$(top_srcdir)/src/stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/collect_tier.c

test_path_collect_SOURCES = \
	test_path_collect.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_path_open_fail_once_SOURCES = \
	test_path_open_fail_once.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_path_read_SOURCES = \
	test_path_read.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_sys_iter_SOURCES = \
	test_sys_iter.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_procfile_parse_SOURCES = \
	test_procfile_parse.c \
	$(top_srcdir)/src/procfile_parse.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_monitor_log_SOURCES = \
	test_monitor_log.c \
	$(top_srcdir)/src/monitor_log.c

test_monitor_release_logging_SOURCES = \
	test_monitor_release_logging.c \
	$(top_srcdir)/src/monitor_release_log.c

test_monitor_options_kv_SOURCES = \
	test_monitor_options_kv.c \
	$(top_srcdir)/src/monitor_options.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/monitor_cli.c

test_pscanf_SOURCES = \
	test_pscanf.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_schema_parse_SOURCES = \
	test_schema_parse.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/dict.c

test_stats_file_format_SOURCES = \
	test_stats_file_format.c \
	$(top_srcdir)/src/stats_file_format.c \
	$(top_srcdir)/src/stats_text_format.c

test_stats_file_format_extra_SOURCES = \
	test_stats_file_format_extra.c \
	$(top_srcdir)/src/stats_file_format.c \
	$(top_srcdir)/src/stats_text_format.c

test_stats_text_format_SOURCES = \
	test_stats_text_format.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_gpu_schema_contract_SOURCES = \
	test_gpu_schema_contract.c

test_dcgm_pkg_uniq_SOURCES = \
	test_dcgm_pkg_uniq.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c

test_cpu_counter_metrics_schema_SOURCES = test_cpu_counter_metrics_schema.c

test_roofline_hw_peak_schema_SOURCES = \
	test_roofline_hw_peak_schema.c

test_roofline_hw_peak_changeover_SOURCES = \
	test_roofline_hw_peak_changeover.c \
	$(top_srcdir)/src/roofline_hw_peak.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c \
	$(top_srcdir)/src/host_edac_mem_topology.c

test_roofline_detect_fixture_SOURCES = \
	test_roofline_detect_fixture.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c \
	$(top_srcdir)/src/host_edac_mem_topology.c

test_nfs_schema_subset_SOURCES = \
	test_nfs_schema_subset.c \
	$(top_srcdir)/src/nfs.c

test_ib_schema_contract_SOURCES = test_ib_schema_contract.c

test_host_schema_contract_SOURCES = test_host_schema_contract.c

test_lustre_schema_contract_SOURCES = test_lustre_schema_contract.c

test_beegfs_schema_contract_SOURCES = test_beegfs_schema_contract.c

test_beegfs_ctl_parse_SOURCES = \
	test_beegfs_ctl_parse.c \
	$(top_srcdir)/src/beegfs_ctl_parse.c

test_lustre_proc_stats_SOURCES = \
	test_lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_proc_stats.c

test_opa_schema_contract_SOURCES = test_opa_schema_contract.c

test_opa_sysfs_map_SOURCES = \
	test_opa_sysfs_map.c \
	$(top_srcdir)/src/opa_sysfs.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_opa_mad_backoff_SOURCES = \
	test_opa_mad_backoff.c \
	$(top_srcdir)/src/opa_mad_backoff.c

test_ib_hca_is_opa_hfi_SOURCES = \
	test_ib_hca_is_opa_hfi.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_opa_lspci_match_SOURCES = test_opa_lspci_match.c
test_opa_lspci_match_CPPFLAGS = $(AM_CPPFLAGS) -DOPA_LSPCI_FIXTURE=\"$(srcdir)/fixtures/opa_lspci_lines.tsv\"

test_lnet_schema_contract_SOURCES = test_lnet_schema_contract.c

test_arm_aarch64_imc_schema_SOURCES = test_arm_aarch64_imc_schema.c

test_metric_profiler_SOURCES = \
	test_metric_profiler.c \
	$(top_srcdir)/src/metric_profiler.c
if METRIC_PROFILER_BACKEND_EBPF
test_metric_profiler_SOURCES += \
	$(top_srcdir)/src/metric_profiler_ebpf.c
endif

test_hwdetect_policy_SOURCES = \
	test_hwdetect_policy.c \
	$(top_srcdir)/src/hwdetect.c \
	$(top_srcdir)/src/gpu_pci_detect.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_hwdetect_lspci_SOURCES = \
	test_hwdetect_lspci.c \
	$(top_srcdir)/src/hwdetect.c \
	$(top_srcdir)/src/gpu_pci_detect.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_gpu_pci_detect_SOURCES = \
	test_gpu_pci_detect.c \
	$(top_srcdir)/src/gpu_pci_detect.c

test_gpu_pci_detect_CPPFLAGS = $(AM_CPPFLAGS) -DGPU_PCI_DETECT_FIXTURE=\"$(srcdir)/fixtures/gpu_lspci_lines.tsv\"

test_dcgm_gpu_dyn_SOURCES = \
	test_dcgm_gpu_dyn.c \
	$(top_srcdir)/src/dcgm_gpu_dyn.c

test_dcgm_gpu_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

test_dcgm_session_SOURCES = \
	test_dcgm_session.c \
	$(top_srcdir)/src/dcgm_session.c \
	$(top_srcdir)/src/dcgm_gpu_dyn.c

test_dcgm_session_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm \
	-DMONITOR_GPU_DCGM_DLOPEN -DDCGM_SESSION_TEST

test_intel_gpu_schema_contract_SOURCES = test_intel_gpu_schema_contract.c
test_intel_gpu_schema_contract_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/src

test_intel_gpu_publish_SOURCES = \
	test_intel_gpu_publish.c \
	test_stats_stub.c \
	$(top_srcdir)/src/intel_gpu.c \
	$(top_srcdir)/src/intel_gpu_xpum_helpers.c \
	$(top_srcdir)/src/xpum_gpu_dyn.c \
	$(top_srcdir)/src/monitor_log.c
test_intel_gpu_publish_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/intel-xpum \
	-DINTEL_GPU_TEST_BUILD

test_xpum_gpu_dyn_SOURCES = \
	test_xpum_gpu_dyn.c \
	$(top_srcdir)/src/xpum_gpu_dyn.c
test_xpum_gpu_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/intel-xpum

test_ib_mad_dyn_SOURCES = \
	test_ib_mad_dyn.c \
	$(top_srcdir)/src/ib_mad_dyn.c
test_ib_mad_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/ibmad-shim \
	-DMONITOR_IB_MAD_DLOPEN

test_opa_mad_dyn_SOURCES = \
	test_opa_mad_dyn.c \
	$(top_srcdir)/src/opa_mad_dyn.c
test_opa_mad_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/oib-shim \
	-DMONITOR_OPA_MAD_DLOPEN

test_ib_port_state_SOURCES = \
	test_ib_port_state.c \
	$(top_srcdir)/src/ib_port_state.c

test_ib_port_collectible_SOURCES = \
	test_ib_port_collectible.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_ib_foreach_hca_port_SOURCES = \
	test_ib_foreach_hca_port.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_ib_family_SOURCES = \
	test_ib_family.c \
	$(top_srcdir)/src/ib_family.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c

if INFINIBAND
test_ib_mad_backoff_SOURCES = \
	test_ib_mad_backoff.c \
	test_ib_mad_stubs.c \
	test_stats_stub.c \
	$(top_srcdir)/src/ib_mad.c \
	$(top_srcdir)/src/monitor_release_log.c

test_ib_mad_decode_SOURCES = \
	test_ib_mad_decode.c \
	test_ib_mad_stubs.c \
	test_stats_stub.c \
	$(top_srcdir)/src/ib_mad.c \
	$(top_srcdir)/src/monitor_release_log.c

test_ib_mad_sw_swap_SOURCES = \
	test_ib_mad_sw_swap.c \
	test_ib_mad_stubs.c \
	test_stats_stub.c \
	$(top_srcdir)/src/ib_mad.c \
	$(top_srcdir)/src/monitor_release_log.c

if IB_MAD_DLOPEN
test_ib_mad_backoff_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c
test_ib_mad_decode_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c
test_ib_mad_sw_swap_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c
test_ib_mad_backoff_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/ibmad-shim
test_ib_mad_decode_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/ibmad-shim
test_ib_mad_sw_swap_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/ibmad-shim
else
# Automake still emits *_CPPFLAGS when the IB_MAD_DLOPEN branch exists; an empty
# program CPPFLAGS would drop AM_CPPFLAGS (-I src) and break non-dlopen builds.
test_ib_mad_backoff_CPPFLAGS = $(AM_CPPFLAGS)
test_ib_mad_decode_CPPFLAGS = $(AM_CPPFLAGS)
test_ib_mad_sw_swap_CPPFLAGS = $(AM_CPPFLAGS)
endif
endif

test_stats_runtime_type_controls_SOURCES = \
	test_stats_runtime_type_controls.c \
	$(top_srcdir)/src/stats_runtime.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/monitor_timing.c \
	$(top_srcdir)/src/dict.c

test_stats_runtime_likwid_begin_order_SOURCES = \
	test_stats_runtime_likwid_begin_order.c \
	$(top_srcdir)/src/stats_runtime.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/monitor_timing.c \
	$(top_srcdir)/src/dict.c

test_stats_runtime_collect_SOURCES = \
	test_stats_runtime_collect.c \
	$(top_srcdir)/src/stats_runtime.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/monitor_timing.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_nvidia_gpu_estimate_SOURCES = \
	test_nvidia_gpu_estimate.c \
	$(top_srcdir)/src/nvidia_gpu_estimate.c

test_nvidia_gpu_dcgm_field_SOURCES = \
	test_nvidia_gpu_dcgm_field.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_field.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c
test_nvidia_gpu_dcgm_field_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

test_daemonize_SOURCES = \
	test_daemonize.c \
	$(top_srcdir)/src/daemonize.c \
	$(top_srcdir)/src/monitor_log.c

test_collect_tier_table_SOURCES = \
	test_collect_tier_table.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_collect_phase_SOURCES = \
	test_collect_phase.c \
	$(top_srcdir)/src/stats_runtime.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/monitor_timing.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_collect_tier_gate_SOURCES = \
	test_collect_tier_gate.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_stats_set_tier_gate_SOURCES = \
	test_stats_set_tier_gate.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

stats_buffer_test_core_sources = \
	$(top_srcdir)/src/stats_buffer.c \
	$(top_srcdir)/src/stats_buffer_rmq.c \
	$(top_srcdir)/src/monitor_release_log.c \
	$(top_srcdir)/src/stats_buffer_rows.c \
	$(top_srcdir)/src/stats_buffer_uts.c \
	$(top_srcdir)/src/stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

if RABBITMQ
test_monitor_cli_SOURCES = \
	test_monitor_cli.c \
	test_monitor_cli_globals.c \
	$(top_srcdir)/src/monitor_cli.c \
	$(top_srcdir)/src/monitor_options.c \
	$(top_srcdir)/src/monitor_log.c

test_ring_buffer_SOURCES = \
	test_ring_buffer.c \
	test_stats_buffer_collect_stubs.c \
	$(stats_buffer_test_core_sources)
test_ring_buffer_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK

test_stats_buffer_collect_SOURCES = \
	test_stats_buffer_collect.c \
	test_stats_buffer_collect_stubs.c \
	$(stats_buffer_test_core_sources)
test_stats_buffer_collect_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK

test_stats_buffer_debug_shm_SOURCES = \
	test_stats_buffer_debug_shm.c \
	test_stats_buffer_collect_stubs.c \
	$(stats_buffer_test_core_sources) \
	$(top_srcdir)/src/stats_buffer_debug_shm.c
test_stats_buffer_debug_shm_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK

test_debug_shm_emit_golden_SOURCES = \
	test_debug_shm_emit_golden.c \
	test_debug_shm_emit_fixture.c \
	test_debug_shm_emit_validate.c \
	$(stats_buffer_test_core_sources) \
	$(top_srcdir)/src/stats_buffer_debug_shm.c
test_debug_shm_emit_golden_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK -DSTATS_BUFFER_TEST_TIME_HOOK -DSTATS_BUFFER_TEST_UTS_HOOK

test_debug_shm_schema_mirror_SOURCES = \
	test_debug_shm_schema_mirror.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/monitor_log.c

test_monitor_timing_SOURCES = \
	test_monitor_timing.c \
	$(top_srcdir)/src/monitor_timing.c
endif

# Link flags/libs match the configured tree (LDFLAGS/LIBS from configure).
test_likwid_rapl_scale_LDADD = $(LDADD) -lm
test_likwid_rapl_support_LDADD = $(LDADD) -lm
test_amd64_rapl_likwid_ready_LDADD = $(LDADD) -lm
test_likwid_pmc_force_env_LDADD = $(LDADD)
test_likwid_pmc_schema_map_LDADD = $(LDADD)
test_likwid_arch_map_spr_LDADD = $(LDADD)
test_likwid_uncore_adapter_LDADD = $(LDADD)
if LIKWID
test_likwid_uncore_adapter_LDADD += -lm
endif
test_dict_LDADD = $(LDADD)
test_host_key_alias_LDADD = $(LDADD)
test_proc_status_order_LDADD = $(LDADD)
test_schema_emit_LDADD = $(LDADD)
test_procfile_kv_LDADD = $(LDADD)
test_collect_str_LDADD = $(LDADD)
test_cpu_counter_dcgm_util_LDADD = $(LDADD)
test_cpu_counter_dcgm_publish_LDADD = $(LDADD)
test_cpu_counter_dcgm_publish_papi_LDADD = $(LDADD)
test_cpu_counter_papi_util_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)
test_stats_buffer_data_append_LDADD = $(LDADD)
test_stats_buffer_uts_LDADD = $(LDADD)
test_stats_buffer_rows_LDADD = $(LDADD)
test_path_collect_LDADD = $(LDADD)
test_path_open_fail_once_LDADD = $(LDADD)
test_path_read_LDADD = $(LDADD)
test_sys_iter_LDADD = $(LDADD)
test_procfile_parse_LDADD = $(LDADD)
test_monitor_log_LDADD = $(LDADD)
test_monitor_release_logging_LDADD = $(LDADD)
test_monitor_options_kv_LDADD = $(LDADD) -lm
test_pscanf_LDADD = $(LDADD)
test_schema_parse_LDADD = $(LDADD)
test_stats_file_format_LDADD = $(LDADD)
test_stats_file_format_extra_LDADD = $(LDADD)
test_stats_text_format_LDADD = $(LDADD)
test_gpu_schema_contract_LDADD = $(LDADD)
test_dcgm_pkg_uniq_LDADD = $(LDADD)
test_cpu_counter_metrics_schema_LDADD = $(LDADD)
test_roofline_hw_peak_schema_LDADD = $(LDADD)
test_roofline_hw_peak_changeover_LDADD = $(LDADD)
test_roofline_detect_fixture_LDADD = $(LDADD)
test_nfs_schema_subset_LDADD = $(LDADD)
test_ib_schema_contract_LDADD = $(LDADD)
test_host_schema_contract_LDADD = $(LDADD)
test_lustre_schema_contract_LDADD = $(LDADD)
test_beegfs_schema_contract_LDADD = $(LDADD)
test_beegfs_ctl_parse_LDADD = $(LDADD)
test_lustre_proc_stats_LDADD = $(LDADD)
test_opa_schema_contract_LDADD = $(LDADD)
test_opa_sysfs_map_LDADD = $(LDADD)
test_opa_mad_backoff_LDADD = $(LDADD)
test_ib_hca_is_opa_hfi_LDADD = $(LDADD)
test_opa_lspci_match_LDADD = $(LDADD)
test_lnet_schema_contract_LDADD = $(LDADD)
test_arm_aarch64_imc_schema_LDADD = $(LDADD)
test_amd_cpuid_match_LDADD = $(LDADD)
test_amd_df_likwid_profiles_LDADD = $(LDADD)
test_amd_x86_uncore_df_schema_LDADD = $(LDADD)
test_metric_profiler_LDADD = $(LDADD)
test_hwdetect_policy_LDADD = $(LDADD)
test_hwdetect_lspci_LDADD = $(LDADD)
test_gpu_pci_detect_LDADD = $(LDADD)
test_dcgm_gpu_dyn_LDADD = $(LDADD) -ldl
test_dcgm_session_LDADD = $(LDADD) -ldl
test_intel_gpu_schema_contract_LDADD = $(LDADD)
test_intel_gpu_publish_LDADD = $(LDADD) -ldl
test_xpum_gpu_dyn_LDADD = $(LDADD) -ldl
test_ib_mad_dyn_LDADD = $(LDADD) -ldl
test_opa_mad_dyn_LDADD = $(LDADD) -ldl
test_ib_port_state_LDADD = $(LDADD)
test_ib_port_collectible_LDADD = $(LDADD)
test_ib_foreach_hca_port_LDADD = $(LDADD)
test_ib_family_LDADD = $(LDADD)
if INFINIBAND
test_ib_mad_backoff_LDADD = $(LDADD)
test_ib_mad_decode_LDADD = $(LDADD)
test_ib_mad_sw_swap_LDADD = $(LDADD)
if IB_MAD_DLOPEN
test_ib_mad_backoff_LDADD += -ldl
test_ib_mad_decode_LDADD += -ldl
test_ib_mad_sw_swap_LDADD += -ldl
endif
endif
test_stats_runtime_type_controls_LDADD = $(LDADD) -lm
test_stats_runtime_likwid_begin_order_LDADD = $(LDADD) -lm
test_stats_runtime_collect_LDADD = $(LDADD) -lm
test_nvidia_gpu_estimate_LDADD = $(LDADD) -lm
test_nvidia_gpu_dcgm_field_LDADD = $(LDADD)
test_daemonize_LDADD = $(LDADD)
test_collect_tier_table_LDADD = $(LDADD)
test_collect_phase_LDADD = $(LDADD) -lm
test_collect_tier_gate_LDADD = $(LDADD)
test_stats_set_tier_gate_LDADD = $(LDADD)
if RABBITMQ
test_monitor_cli_LDADD = $(LDADD)
test_ring_buffer_LDADD = $(LDADD)
test_stats_buffer_collect_LDADD = $(LDADD)
test_stats_buffer_debug_shm_LDADD = $(LDADD)
test_debug_shm_emit_golden_LDADD = $(LDADD)
test_debug_shm_schema_mirror_LDADD = $(LDADD)
test_monitor_timing_LDADD = $(LDADD) -lm
endif
