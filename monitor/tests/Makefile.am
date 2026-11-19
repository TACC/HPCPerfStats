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

monitor_unit_cppflags += -DRABBITMQ $(MONITOR_AMQP_TIMEOUT_CPPFLAGS)

if MONITOR_USE_DCGM_VENDOR_INCLUDES
monitor_unit_cppflags += -I$(top_srcdir)/third_party/nvidia-dcgm
endif

# Keep assert() live under release -DNDEBUG (drivers put required calls inside asserts).
AM_CPPFLAGS = -include $(srcdir)/test_assert_live.h -I$(top_builddir)/src -I$(top_srcdir)/src $(monitor_unit_cppflags)

check_PROGRAMS = test_assert_live test_cpuid_signature test_amd_cpuid_match test_amd_x86_uncore_df_schema test_rapl_powercap test_host_edac_mem_topology test_intel_skx_imc_schema test_intel_icx_imc_schema test_intel_spr_imc_schema test_intel_emr_imc_schema test_intel_gnr_imc_schema test_intel_srf_imc_schema test_string1 test_secure_path test_dyn_lib_path test_stats_buffer_data_append test_stats_buffer_uts test_stats_buffer_rows test_stats_buffer_rmq_policy test_path_collect test_path_open_fail_once test_path_read test_sys_iter test_procfile_parse test_monitor_log test_monitor_release_logging test_monitor_options_kv test_pscanf test_schema_parse test_stats_text_format test_gpu_schema_contract test_cpu_counter_metrics_schema test_cpu_counter_metrics_schema_arm test_roofline_hw_peak_schema test_roofline_hw_peak_changeover test_roofline_detect_fixture test_roofline_hw_peak_identity test_nfs_schema_subset test_nfs_mountstats_parse test_ib_schema_contract test_host_schema_contract test_lustre_schema_contract test_lustre_proc_stats test_beegfs_schema_contract test_beegfs_mount test_beegfs_tcp test_opa_schema_contract test_opa_sysfs_map test_opa_mad_backoff test_ib_hca_is_opa_hfi test_opa_lspci_match test_lnet_schema_contract test_arm_aarch64_imc_schema test_arm_imc_scf test_metric_profiler test_hwdetect_policy test_hwdetect_lspci test_gpu_pci_detect test_intel_gpu_schema_contract test_intel_gpu_publish test_intel_gpu_xpumcli_parse test_xpum_gpu_dyn test_dcgm_gpu_dyn test_dcgm_session test_ib_mad_dyn test_opa_mad_dyn test_opa_mad_missing_lib test_ib_port_state test_ib_port_collectible test_ib_foreach_hca_port test_ib_family test_stats_runtime_type_controls test_stats_runtime_begin_order test_stats_runtime_collect test_collect_tier_table test_collect_phase test_collect_tier_gate test_stats_set_tier_gate test_stats_type_get test_dcgm_watch_policy test_dict test_host_key_alias test_proc_status_order test_schema_emit test_procfile_kv test_collect_str test_nvidia_gpu_estimate test_nvidia_gpu_dcgm_field test_nvidia_gpu_dcgm_profiles test_daemonize test_proc_filter test_job_proc_discover test_job_gpu_assign test_job_sched_affinity test_job_discovery_marks test_job_cgroup_mem test_host_mem_job_schema_contract test_stats_sentinel_catalog test_stats_set_missing test_collect_sentinels test_ib_sentinels test_gpu_power_sentinels test_proc_prune_vanished test_lustre_osc_shared_sum test_lustre_zero_fill test_beegfs_io test_lustre_llite_paths test_debug_shm_sentinels test_fault_inject_matrix test_fault_inject_hw test_fault_inject_fs test_fault_inject_cpu \
	test_fault_inject_opa test_fault_inject_intel_gpu test_fault_inject_xpumcli test_fault_inject_x86_publish \
	test_fault_inject_power test_fault_inject_arm_publish test_fault_inject_x86_uncore \
	test_fault_inject_mem_job
if INFINIBAND
check_PROGRAMS += test_ib_mad_backoff test_ib_mad_decode test_ib_mad_sw_swap
endif
# Pure arm / x86 publish math, the direct-perf readers of both backends (fake sysfs + pipe fds)
# and host_power_socket run on every host (portable C).
check_PROGRAMS += test_nvidia_gpu_nvml_convert test_nvidia_gpu_nvml
check_PROGRAMS += test_cpu_counter_arm_util test_cpu_counter_arm_publish \
	test_perf_util test_arm_core_perf \
	test_cpu_counter_x86_publish test_x86_core_perf test_x86_core_perf_bridge test_x86_uncore_perf \
	test_x86_rapl test_arm_power_hwmon test_arm_power_socket
check_PROGRAMS += test_monitor_cli test_ring_buffer test_stats_buffer_collect test_stats_buffer_debug_shm test_debug_shm_emit_golden test_debug_shm_schema_mirror test_monitor_timing

# Script tests run through the Automake test driver, so they report PASS/FAIL/SKIP lines like
# the C drivers: each name is a generated wrapper that runs $(srcdir)/<name>.sh with the build
# tree root as its argument (exit 77 is SKIP).
monitor_script_tests = test_shm_validation_lib test_shm_message_correctness test_shm_message_golden \
	test_check_sentinel_error_paths test_independent_read_check test_consumer_parse_shm_capture \
	test_fault_inject_verify test_fault_ci_files test_build_scripts_py36
check_SCRIPTS = $(monitor_script_tests)
CLEANFILES = $(monitor_script_tests)
$(monitor_script_tests): Makefile
	$(AM_V_GEN)printf '#!/bin/sh\nexec $(SHELL) "%s" "%s" "$$@"\n' \
	  '$(abs_srcdir)/$@.sh' '$(abs_top_builddir)' >$@ && chmod +x $@

TESTS = $(check_PROGRAMS) $(monitor_script_tests)

# Run from source (not generated): avoids Automake distdir-am needing a built .sh on some versions.
check-local:
	$(SHELL) '$(srcdir)/test_monitor_configure_help.sh.in'
	$(SHELL) '$(srcdir)/test_static_bundle_configure_flags.sh'
	$(SHELL) '$(srcdir)/test_prepare_rpmbuild_stampede3.sh'
	$(SHELL) '$(srcdir)/test_prepare_rpmbuild_ls6.sh'
	$(SHELL) '$(srcdir)/test_prepare_rpmbuild_horizon.sh'
	$(SHELL) '$(srcdir)/test_prepare_rpmbuild_measure_flag.sh'
	$(SHELL) '$(srcdir)/test_metric_profiler_link_stubs.sh'
	$(SHELL) '$(srcdir)/test_gpu_lspci_detect_parity.sh'
	$(SHELL) '$(srcdir)/test_monitor_configure_amd_auto.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_monitor_configure_intel_gpu_auto.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_make_dist_extra_dist_prereqs.sh'
	$(SHELL) '$(srcdir)/test_valgrind_cpp_linter_scripts.sh'
	$(SHELL) '$(srcdir)/test_prepare_monitor_tree_clean.sh'
	$(SHELL) '$(srcdir)/test_build_accel.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_hpc_debug_build_configure.sh'
	$(SHELL) '$(srcdir)/test_rpm_debug_shm_verify.sh'
	$(SHELL) '$(srcdir)/test_monitor_release_logging_contract.sh'
	$(SHELL) '$(srcdir)/test_validate_stampede3_profile.sh'
	$(SHELL) '$(srcdir)/test_net_debug_trace_format.sh'
	$(SHELL) '$(srcdir)/test_debug_log_newline_contract.sh'
	$(SHELL) '$(srcdir)/test_cpu_backend_configure.sh'
	$(SHELL) '$(srcdir)/test_script_word_lists.sh'
	$(SHELL) '$(srcdir)/test_configure_rabbitmq_static_probe.sh'
	$(SHELL) '$(srcdir)/test_stats_buffer_rmq_reconnect_contract.sh'
	$(SHELL) '$(srcdir)/test_amd_legacy_removed.sh'
	$(SHELL) '$(srcdir)/test_intel_legacy_pmc_removed.sh'
	$(SHELL) '$(srcdir)/test_lshw_stub.sh'
	$(SHELL) '$(srcdir)/test_canary_gemm_load.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_canary_counter_check.sh' || test $$? -eq 77
	$(SHELL) '$(srcdir)/test_rpm_debug_counter_check.sh' || test $$? -eq 77
	python3 '$(srcdir)/test_correctness_matrix.py'
	$(SHELL) '$(srcdir)/test_perf_raw_flops_check.sh'
	$(SHELL) '$(srcdir)/test_run_warning_gate.sh'
	$(SHELL) '$(srcdir)/test_service_unit_lshw.sh'
	$(SHELL) '$(srcdir)/test_hpcperfstats_service_sandbox.sh'
	$(SHELL) '$(srcdir)/test_job_shared_emit_debug_shm_mirror.sh'
	$(SHELL) '$(srcdir)/test_ib_mad_dlopen_link_deps.sh'
	python3 '$(top_srcdir)/scripts/check_emitted_variable_names.py'
	$(SHELL) '$(top_srcdir)/scripts/check_unsafe_c_patterns.sh'
	@if test "x$${RUN_RMQ_INTEGRATION:-0}" = "x1"; then \
	  $(MAKE) check-rabbitmq-integration; \
	fi

EXTRA_DIST = test_monitor_configure_help.sh.in test_static_bundle_configure_flags.sh test_prepare_rpmbuild_stampede3.sh test_prepare_rpmbuild_ls6.sh test_prepare_rpmbuild_horizon.sh test_prepare_rpmbuild_measure_flag.sh test_metric_profiler_link_stubs.sh test_ib_mad_dyn.c test_opa_mad_dyn.c test_gpu_lspci_detect_parity.sh test_monitor_configure_amd_auto.sh test_monitor_configure_intel_gpu_auto.sh fixtures/gpu_lspci_lines.tsv fixtures/opa_lspci_lines.tsv fixtures/nfs/mountstats_v3_v4.txt fixtures/stampede3_lspci_profiles/README.md fixtures/stampede3_lspci_profiles/skx fixtures/stampede3_lspci_profiles/clx fixtures/stampede3_lspci_profiles/icx fixtures/stampede3_lspci_profiles/spr fixtures/stampede3_lspci_profiles/h100 fixtures/stampede3_lspci_profiles/pvc fixtures/stampede3_lspci_profiles/amd-rtx fixtures/ls6_lspci_profiles/README.md fixtures/ls6_lspci_profiles/genoa fixtures/ls6_lspci_profiles/milan fixtures/ls6_lspci_profiles/a100 fixtures/vista_lspci_profiles/README.md fixtures/vista_lspci_profiles/gg fixtures/vista_lspci_profiles/gh fixtures/horizon_lspci_profiles/README.md fixtures/horizon_lspci_profiles/gb test_validate_stampede3_profile.sh test_make_dist_extra_dist_prereqs.sh test_valgrind_cpp_linter_scripts.sh test_prepare_monitor_tree_clean.sh test_build_accel.sh test_hpc_debug_build_configure.sh test_rpm_debug_shm_verify.sh test_monitor_release_logging_contract.sh test_net_debug_trace_format.sh test_debug_log_newline_contract.sh test_cpu_backend_configure.sh test_script_word_lists.sh test_configure_rabbitmq_static_probe.sh test_stats_buffer_rmq_reconnect_contract.sh test_amd_legacy_removed.sh test_intel_legacy_pmc_removed.sh test_lshw_stub.sh test_canary_gemm_load.sh test_canary_counter_check.sh test_rpm_debug_counter_check.sh test_counter_accuracy_lib.py test_correctness_summary.py test_correctness_matrix.py test_perf_raw_flops_check.sh test_run_warning_gate.sh test_service_unit_lshw.sh test_hpcperfstats_service_sandbox.sh test_job_shared_emit_debug_shm_mirror.sh test_ib_mad_dlopen_link_deps.sh test_dcgm_session.c run_tests.sh README.md test_ring_buffer.c test_ring_buffer_stubs.c test_stats_buffer_collect.c test_stats_buffer_collect_stubs.c test_stats_buffer_collect_stubs.h test_debug_shm_emit_fixture.c test_debug_shm_emit_fixture.h test_debug_shm_emit_validate.c test_debug_shm_emit_validate.h test_debug_shm_emit_golden.c test_debug_shm_schema_mirror.c test_shm_message_correctness.sh test_shm_message_golden.sh test_check_sentinel_error_paths.sh test_independent_read_check.sh test_consumer_parse_shm_capture.sh test_fault_inject_verify.sh test_fault_ci_files.sh test_shm_validation_lib.sh test_shm_validation_lib.py test_build_scripts_py36.sh test_build_scripts_py36.py test_host_edac_mem_topology.c expected/debug_shm_fast.txt expected/debug_shm_full.txt expected/synthetic_fixture/schema expected/synthetic_fixture/fast expected/synthetic_fixture/full expected/synthetic_fixture/monitor-build-capabilities.json expected/synthetic_fixture/expectations_synthetic_debug_tier1.json test_stats_buffer_rows.c test_stats_buffer_rmq_policy.c test_stats_runtime_collect.c test_ib_family.c test_ib_mad_backoff.c test_ib_mad_decode.c test_ib_mad_stubs.c test_stats_stub.h test_stats_missing_stub.h test_rm_tree.h test_ib_mad_sw_swap.c test_nvidia_gpu_estimate.c test_nvidia_gpu_dcgm_profiles.c test_roofline_detect_fixture.c test_daemonize.c test_path_open_fail_once.c test_monitor_timing.c test_stats_text_format.c test_arm_aarch64_imc_schema.c test_rabbitmq_integration.sh rmq_integration_validate.py requirements-rabbitmq-integration.txt requirements-cpp-linter.txt scripts/bootstrap_local_rabbitmq.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.allowlist $(top_srcdir)/scripts/check_emitted_variable_names.py $(top_srcdir)/scripts/check_sentinel_error_paths.py $(top_srcdir)/scripts/check_sentinel_error_paths.allowlist $(top_srcdir)/scripts/consumer_parse_check.py $(top_srcdir)/scripts/fault_inject_check.py $(top_srcdir)/scripts/check_fault_ci_files.py $(top_srcdir)/scripts/lib/fault_ci_files.py $(top_srcdir)/scripts/fault_inject_verify.sh $(top_srcdir)/scripts/lib/log_compiler_exec.sh $(top_srcdir)/scripts/lib/independent_read.py $(top_srcdir)/scripts/emit_build_capabilities.py $(top_srcdir)/scripts/build_message_expectations.py $(top_srcdir)/scripts/validate_shm_messages.py $(top_srcdir)/scripts/lib/message_parse.py $(top_srcdir)/scripts/lib/row_validate.py $(top_srcdir)/scripts/lib/sentinel_catalog.py $(top_srcdir)/scripts/lib/sentinel_validate.py $(top_srcdir)/scripts/lib/payload_parse.py $(top_srcdir)/scripts/lib/device_validate.py $(top_srcdir)/scripts/lib/listend_contract.py $(top_srcdir)/scripts/lib/host_live_probes.py $(top_srcdir)/scripts/lib/value_plausibility.py $(top_srcdir)/scripts/lib/live_spot_check.py $(top_srcdir)/scripts/lib/golden_diff.py $(top_srcdir)/scripts/lib/cross_sample_validate.py $(top_srcdir)/scripts/lib/cross_sample_stimulus.py $(top_srcdir)/scripts/lib/shm_snapshot.py $(top_srcdir)/scripts/lib/daemon_conf.py $(top_srcdir)/scripts/lib/papi_shm_validate.py $(top_srcdir)/scripts/lib/tacc_system_profiles.py $(top_srcdir)/scripts/validate_stampede3_profile.sh $(top_srcdir)/scripts/lib/__init__.py $(top_srcdir)/scripts/canary_gemm_load.sh $(top_srcdir)/scripts/canary_counter_check.sh $(top_srcdir)/scripts/lib/counter_accuracy.py $(top_srcdir)/scripts/lib/correctness_summary.py $(top_srcdir)/scripts/correctness_matrix.py $(top_srcdir)/scripts/lib/correctness_classes.tsv $(top_srcdir)/scripts/lib/correctness_expected.tsv $(top_srcdir)/scripts/rpm_debug_shm_verify.sh $(top_srcdir)/scripts/perf_raw_flops_check.sh $(top_srcdir)/scripts/lib/build_accel.sh $(top_srcdir)/scripts/run_warning_gate.sh $(top_srcdir)/scripts/unreachable_functions.allowlist

.PHONY: check-rabbitmq-integration
check-rabbitmq-integration:
	$(SHELL) '$(srcdir)/test_rabbitmq_integration.sh' '$(top_builddir)'

test_rapl_powercap_SOURCES = \
	test_rapl_powercap.c \
	$(top_srcdir)/src/rapl_powercap.c

test_cpuid_signature_SOURCES = \
	test_cpuid_signature.c \
	$(top_srcdir)/src/intel_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c

test_amd_cpuid_match_SOURCES = \
	test_amd_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c

test_amd_x86_uncore_df_schema_SOURCES = test_amd_x86_uncore_df_schema.c

test_host_edac_mem_topology_SOURCES = \
	test_host_edac_mem_topology.c \
	$(top_srcdir)/src/host_edac_mem_topology.c
# Regression: getpid() was used without <unistd.h> (implicit declaration; C99 forbids it).
test_host_edac_mem_topology_CFLAGS = $(AM_CFLAGS) -Werror=implicit-function-declaration

test_intel_skx_imc_schema_SOURCES = test_intel_skx_imc_schema.c
test_intel_icx_imc_schema_SOURCES = test_intel_icx_imc_schema.c
test_intel_spr_imc_schema_SOURCES = test_intel_spr_imc_schema.c
test_intel_emr_imc_schema_SOURCES = test_intel_emr_imc_schema.c
test_intel_gnr_imc_schema_SOURCES = test_intel_gnr_imc_schema.c
test_intel_srf_imc_schema_SOURCES = test_intel_srf_imc_schema.c

test_stats_sentinel_catalog_SOURCES = test_stats_sentinel_catalog.c

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

test_proc_filter_SOURCES = \
	test_proc_filter.c \
	$(top_srcdir)/src/proc_filter.c

test_job_proc_discover_SOURCES = \
	test_job_proc_discover.c \
	$(top_srcdir)/src/job_proc_discover.c \
	$(top_srcdir)/src/job_cgroup_mem.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/monitor_log.c

test_job_gpu_assign_SOURCES = \
	test_job_gpu_assign.c \
	$(top_srcdir)/src/job_gpu_assign.c \
	$(top_srcdir)/src/job_proc_discover.c \
	$(top_srcdir)/src/job_cgroup_mem.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/monitor_log.c

test_job_sched_affinity_SOURCES = \
	test_job_sched_affinity.c \
	$(top_srcdir)/src/job_sched_affinity.c \
	$(top_srcdir)/src/job_gpu_assign.c \
	$(top_srcdir)/src/job_proc_discover.c \
	$(top_srcdir)/src/job_cgroup_mem.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/monitor_log.c

test_job_discovery_marks_SOURCES = \
	test_job_discovery_marks.c \
	$(top_srcdir)/src/job_proc_discover.c \
	$(top_srcdir)/src/job_cgroup_mem.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/job_emit_filter.c \
	$(top_srcdir)/src/job_gpu_assign.c \
	$(top_srcdir)/src/job_sched_affinity.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/monitor_log.c

test_job_cgroup_mem_SOURCES = \
	test_job_cgroup_mem.c \
	$(top_srcdir)/src/job_cgroup_mem.c \
	$(top_srcdir)/src/host_mem_job.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_host_mem_job_schema_contract_SOURCES = \
	test_host_mem_job_schema_contract.c

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
	test_stats_missing_stub.c \
	test_stats_stub.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_cpu_counter_arm_util_SOURCES = \
	test_cpu_counter_arm_util.c \
	$(top_srcdir)/src/cpu_counter_metrics_arm_util.c

test_cpu_counter_arm_publish_SOURCES = \
	test_cpu_counter_arm_publish.c \
	test_stats_stub.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_arm_publish.c

test_cpu_counter_x86_publish_SOURCES = \
	test_cpu_counter_x86_publish.c \
	test_stats_stub.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_x86_publish.c

test_x86_core_perf_SOURCES = \
	test_x86_core_perf.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/x86_core_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

test_x86_core_perf_bridge_SOURCES = \
	test_x86_core_perf_bridge.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/x86_core_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

# x86_uncore_perf on a fake sysfs; the test records stats_set / stats_set_missing itself.
test_x86_uncore_perf_SOURCES = \
	test_x86_uncore_perf.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/x86_uncore_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

test_x86_rapl_SOURCES = \
	test_x86_rapl.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/x86_rapl.c \
	$(top_srcdir)/src/rapl_powercap.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

test_perf_util_SOURCES = \
	test_perf_util.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/perf_util.c

test_arm_core_perf_SOURCES = \
	test_arm_core_perf.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/arm_core_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

test_arm_power_hwmon_SOURCES = \
	test_arm_power_hwmon.c \
	$(top_srcdir)/src/arm_power_hwmon.c \
	$(top_srcdir)/src/monitor_log.c

test_arm_power_socket_SOURCES = \
	test_arm_power_socket.c \
	test_stats_stub.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/arm_power_socket.c \
	$(top_srcdir)/src/arm_power_hwmon.c \
	$(top_srcdir)/src/monitor_log.c

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
	$(top_srcdir)/src/monitor_release_log.c \
	$(top_srcdir)/src/stats_buffer_rows.c \
	$(top_srcdir)/src/stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/collect_tier.c

test_stats_buffer_rmq_policy_SOURCES = \
	test_stats_buffer_rmq_policy.c \
	$(top_srcdir)/src/stats_buffer_rmq_policy.c
test_stats_buffer_rmq_policy_CPPFLAGS = $(AM_CPPFLAGS)
test_stats_buffer_rmq_policy_LDADD = $(LDADD)

test_path_collect_SOURCES = \
	test_path_collect.c \
	test_stats_missing_stub.c \
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

test_stats_text_format_SOURCES = \
	test_stats_text_format.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_gpu_schema_contract_SOURCES = \
	test_gpu_schema_contract.c

# Regression: built with -DNDEBUG like a release build; fails if assert() is compiled out.
test_assert_live_SOURCES = test_assert_live.c test_assert_live.h
test_assert_live_CPPFLAGS = $(AM_CPPFLAGS) -DNDEBUG

test_cpu_counter_metrics_schema_SOURCES = test_cpu_counter_metrics_schema.c
# Same contract with the Grace `arm` schema (D1 keys at W=64) on every host.
test_cpu_counter_metrics_schema_arm_SOURCES = test_cpu_counter_metrics_schema.c
test_cpu_counter_metrics_schema_arm_CPPFLAGS = $(AM_CPPFLAGS) \
	-UMONITOR_CPU_BACKEND_X86 -DMONITOR_CPU_BACKEND_ARM

test_roofline_hw_peak_schema_SOURCES = \
	test_roofline_hw_peak_schema.c

test_roofline_hw_peak_changeover_SOURCES = \
	test_roofline_hw_peak_changeover.c \
	$(top_srcdir)/src/roofline_hw_peak.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c \
	$(top_srcdir)/src/roofline_hw_peak_identity.c \
	$(top_srcdir)/src/host_edac_mem_topology.c \
	$(top_srcdir)/src/cpuid.c \
	$(top_srcdir)/src/intel_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_roofline_detect_fixture_SOURCES = \
	test_roofline_detect_fixture.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c \
	$(top_srcdir)/src/roofline_hw_peak_identity.c \
	$(top_srcdir)/src/host_edac_mem_topology.c \
	$(top_srcdir)/src/cpuid.c \
	$(top_srcdir)/src/intel_cpuid_match.c \
	$(top_srcdir)/src/amd_cpuid_match.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_roofline_hw_peak_identity_SOURCES = \
	test_roofline_hw_peak_identity.c \
	$(top_srcdir)/src/roofline_hw_peak_identity.c

test_nfs_schema_subset_SOURCES = \
	test_nfs_schema_subset.c \
	$(top_srcdir)/src/nfs.c

test_nfs_mountstats_parse_SOURCES = \
	test_nfs_mountstats_parse.c \
	$(top_srcdir)/src/nfs.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_nfs_mountstats_parse_CPPFLAGS = $(AM_CPPFLAGS) \
	-DNFS_MOUNTSTATS_FIXTURE=\"$(srcdir)/fixtures/nfs/mountstats_v3_v4.txt\"

test_ib_schema_contract_SOURCES = test_ib_schema_contract.c

test_host_schema_contract_SOURCES = test_host_schema_contract.c

test_lustre_schema_contract_SOURCES = test_lustre_schema_contract.c

test_beegfs_schema_contract_SOURCES = test_beegfs_schema_contract.c

test_beegfs_mount_SOURCES = \
	test_beegfs_mount.c \
	$(top_srcdir)/src/beegfs_mount.c

test_beegfs_tcp_SOURCES = \
	test_beegfs_tcp.c \
	$(top_srcdir)/src/beegfs_tcp.c

test_lustre_proc_stats_SOURCES = \
	test_lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_proc_stats.c

test_opa_schema_contract_SOURCES = test_opa_schema_contract.c

test_opa_sysfs_map_SOURCES = \
	test_opa_sysfs_map.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/opa_sysfs.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_opa_mad_backoff_SOURCES = \
	test_opa_mad_backoff.c \
	$(top_srcdir)/src/opa_mad_backoff.c \
	$(top_srcdir)/src/monitor_log.c

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
test_arm_imc_scf_SOURCES = \
	test_arm_imc_scf.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/arm_imc_scf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c

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
	$(top_srcdir)/src/dcgm_gpu_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c

test_dcgm_gpu_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

test_dcgm_session_SOURCES = \
	test_dcgm_session.c \
	$(top_srcdir)/src/dcgm_session.c \
	$(top_srcdir)/src/dcgm_gpu_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c \
	$(top_srcdir)/src/monitor_log.c

test_dcgm_session_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm \
	-DMONITOR_GPU_DCGM_DLOPEN -DDCGM_SESSION_TEST

test_intel_gpu_schema_contract_SOURCES = test_intel_gpu_schema_contract.c
test_intel_gpu_schema_contract_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/src

test_intel_gpu_publish_SOURCES = \
	test_intel_gpu_publish.c \
	test_stats_stub.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/intel_gpu.c \
	$(top_srcdir)/src/intel_gpu_xpum_helpers.c \
	$(top_srcdir)/src/intel_gpu_xpumcli.c \
	$(top_srcdir)/src/xpum_gpu_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c \
	$(top_srcdir)/src/monitor_log.c
test_intel_gpu_publish_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/intel-xpum \
	-DINTEL_GPU_TEST_BUILD

test_intel_gpu_xpumcli_parse_SOURCES = \
	test_intel_gpu_xpumcli_parse.c \
	test_stats_stub.c \
	test_stats_missing_stub.c \
	$(top_srcdir)/src/intel_gpu_xpumcli.c \
	$(top_srcdir)/src/monitor_log.c
test_intel_gpu_xpumcli_parse_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/src

test_xpum_gpu_dyn_SOURCES = \
	test_xpum_gpu_dyn.c \
	$(top_srcdir)/src/xpum_gpu_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c
test_xpum_gpu_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/intel-xpum

test_ib_mad_dyn_SOURCES = \
	test_ib_mad_dyn.c \
	$(top_srcdir)/src/ib_mad_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c
test_ib_mad_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/ibmad-shim \
	-DMONITOR_IB_MAD_DLOPEN

test_opa_mad_dyn_SOURCES = \
	test_opa_mad_dyn.c \
	$(top_srcdir)/src/opa_mad_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c
test_opa_mad_dyn_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/oib-shim \
	-DMONITOR_OPA_MAD_DLOPEN

# opa.c is #included by the driver (static per-port collect); MAD-dlopen build, no liboib_utils.
test_opa_mad_missing_lib_SOURCES = \
	test_opa_mad_missing_lib.c \
	$(top_srcdir)/src/opa_mad_dyn.c \
	$(top_srcdir)/src/dyn_lib_path.c \
	$(top_srcdir)/src/opa_mad_backoff.c \
	$(top_srcdir)/src/opa_sysfs.c \
	$(top_srcdir)/src/monitor_release_log.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c
test_opa_mad_missing_lib_CPPFLAGS = $(test_opa_mad_dyn_CPPFLAGS) -DMONITOR_WITH_OPA
test_opa_mad_missing_lib_LDADD = $(LDADD) -ldl

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
	test_stats_missing_stub.c \
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
test_ib_mad_backoff_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c $(top_srcdir)/src/dyn_lib_path.c
test_ib_mad_decode_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c $(top_srcdir)/src/dyn_lib_path.c
test_ib_mad_sw_swap_SOURCES += $(top_srcdir)/src/ib_mad_dyn.c $(top_srcdir)/src/dyn_lib_path.c
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

# Plain registry begin order on every backend (no type begins before another).
test_stats_runtime_begin_order_SOURCES = \
	test_stats_runtime_begin_order.c \
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
	$(top_srcdir)/src/nvidia_gpu_dcgm_field.c
test_nvidia_gpu_dcgm_field_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

test_nvidia_gpu_nvml_convert_SOURCES = \
	test_nvidia_gpu_nvml_convert.c \
	$(top_srcdir)/src/nvidia_gpu_nvml_convert.c
test_nvidia_gpu_nvml_convert_LDADD = $(LDADD) -lm

# NVML + GPM source with a stubbed API table (no driver); vendored nvml.h for struct layouts.
test_nvidia_gpu_nvml_SOURCES = \
	test_nvidia_gpu_nvml.c \
	$(top_srcdir)/src/nvidia_gpu_nvml.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_field.c \
	$(top_srcdir)/src/nvidia_gpu_nvml_convert.c \
	$(top_srcdir)/src/dyn_lib_path.c
test_nvidia_gpu_nvml_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-nvml \
	-I$(top_srcdir)/third_party/nvidia-dcgm
test_nvidia_gpu_nvml_LDADD = $(LDADD) -ldl -lm

test_nvidia_gpu_dcgm_profiles_SOURCES = \
	test_nvidia_gpu_dcgm_profiles.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_profiles.c
test_nvidia_gpu_dcgm_profiles_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

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
	test_stats_missing_stub.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_stats_set_tier_gate_SOURCES = \
	test_stats_set_tier_gate.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_collect_sentinels_SOURCES = \
	test_collect_sentinels.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_ib_sentinels_SOURCES = \
	test_ib_sentinels.c \
	$(top_srcdir)/src/ib_family.c \
	$(top_srcdir)/src/ib_common.c \
	$(top_srcdir)/src/ib_port_state.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/opa_sysfs.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_gpu_power_sentinels_SOURCES = \
	test_gpu_power_sentinels.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_field.c \
	$(top_srcdir)/src/intel_rapl.c \
	$(top_srcdir)/src/amd64_rapl.c \
	$(top_srcdir)/src/amd_processor.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/arm_power_hwmon.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_gpu_power_sentinels_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/third_party/nvidia-dcgm

test_proc_prune_vanished_SOURCES = \
	test_proc_prune_vanished.c \
	$(top_srcdir)/src/proc.c \
	$(top_srcdir)/src/proc_status.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/host_key_alias.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_lustre_osc_shared_sum_SOURCES = \
	test_lustre_osc_shared_sum.c \
	$(top_srcdir)/src/osc.c \
	$(top_srcdir)/src/lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_obd_to_mnt.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_lustre_zero_fill_SOURCES = \
	test_lustre_zero_fill.c \
	$(top_srcdir)/src/osc.c \
	$(top_srcdir)/src/mdc.c \
	$(top_srcdir)/src/lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_obd_to_mnt.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_beegfs_io_SOURCES = \
	test_beegfs_io.c \
	$(top_srcdir)/src/beegfs.c \
	$(top_srcdir)/src/beegfs_mount.c \
	$(top_srcdir)/src/beegfs_tcp.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_lustre_llite_paths_SOURCES = \
	test_lustre_llite_paths.c \
	$(top_srcdir)/src/llite.c \
	$(top_srcdir)/src/lustre_proc_stats.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

# DEBUG sentinel tooling: compiled with -DDEBUG in every build so the provenance and the
# failure-site coverage gate run on release make check too.
test_debug_shm_sentinels_SOURCES = \
	test_debug_shm_sentinels.c \
	$(top_srcdir)/src/stats_sentinel_debug.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_debug_shm_sentinels_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG

test_fault_inject_matrix_SOURCES = \
	test_fault_inject_matrix.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/cpu.c \
	$(top_srcdir)/src/ps.c \
	$(top_srcdir)/src/vfs.c \
	$(top_srcdir)/src/osc.c \
	$(top_srcdir)/src/mdc.c \
	$(top_srcdir)/src/procfile_parse.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_obd_to_mnt.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/stats_sentinel_debug.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_fault_inject_matrix_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG

# Hardware fault points over the fakes: MAD and DCGM run through their dlopen test hooks, so the
# IB / DCGM dlopen defines are forced whatever this build configured.
test_fault_inject_hw_SOURCES = \
	test_fault_inject_hw.c \
	test_fake_perf.c \
	test_fake_perf.h \
	$(top_srcdir)/src/arm_core_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/arm_power_hwmon.c \
	$(top_srcdir)/src/arm_power_socket.c \
	$(top_srcdir)/src/ib_family.c \
	$(top_srcdir)/src/ib_mad.c \
	$(top_srcdir)/src/ib_mad_dyn.c \
	$(top_srcdir)/src/nvidia_gpu.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_watch.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_profiles.c \
	$(top_srcdir)/src/nvidia_gpu_dcgm_field.c \
	$(top_srcdir)/src/nvidia_gpu_nvml.c \
	$(top_srcdir)/src/nvidia_gpu_nvml_convert.c \
	$(top_srcdir)/src/nvidia_gpu_estimate.c \
	$(top_srcdir)/src/dcgm_session.c \
	$(top_srcdir)/src/dcgm_gpu_dyn.c \
	$(top_srcdir)/src/dcgm_watch_policy.c \
	$(top_srcdir)/src/dyn_lib_path.c \
	$(top_srcdir)/src/monitor_release_log.c \
	$(top_srcdir)/src/stats_sentinel_debug.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_fault_inject_hw_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG -DMONITOR_WITH_INFINIBAND \
	-DMONITOR_IB_MAD_DLOPEN -DMONITOR_GPU_DCGM_DLOPEN -I$(top_srcdir)/third_party/ibmad-shim \
	-I$(top_srcdir)/third_party/nvidia-dcgm -I$(top_srcdir)/third_party/nvidia-nvml
test_fault_inject_hw_LDADD = $(LDADD) -ldl -lm

# Fault-coverage twins: each collector sentinel test rebuilt with -DDEBUG plus
# test_site_coverage.c, which fails the run when a registered failure site in the binary never
# fired. Together with test_fault_inject_matrix / _hw / _fs / _cpu every src file with a
# stats_set_missing* call site is covered (scripts/lib/fault_ci_files.py,
# test_fault_ci_files).
test_fault_inject_opa_SOURCES = $(test_opa_sysfs_map_SOURCES) test_site_coverage.c
test_fault_inject_opa_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_intel_gpu_SOURCES = $(test_intel_gpu_publish_SOURCES) test_site_coverage.c
test_fault_inject_intel_gpu_CPPFLAGS = $(test_intel_gpu_publish_CPPFLAGS) -DDEBUG
test_fault_inject_intel_gpu_LDADD = $(LDADD) -ldl
test_fault_inject_xpumcli_SOURCES = $(test_intel_gpu_xpumcli_parse_SOURCES) test_site_coverage.c
test_fault_inject_xpumcli_CPPFLAGS = $(test_intel_gpu_xpumcli_parse_CPPFLAGS) -DDEBUG
test_fault_inject_x86_publish_SOURCES = $(test_cpu_counter_x86_publish_SOURCES) test_site_coverage.c
test_fault_inject_x86_publish_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_power_SOURCES = $(test_gpu_power_sentinels_SOURCES) test_site_coverage.c
test_fault_inject_power_CPPFLAGS = $(test_gpu_power_sentinels_CPPFLAGS) -DDEBUG
test_fault_inject_arm_publish_SOURCES = $(test_cpu_counter_arm_publish_SOURCES) test_site_coverage.c
test_fault_inject_arm_publish_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_mem_job_SOURCES = $(test_job_cgroup_mem_SOURCES) test_site_coverage.c
test_fault_inject_mem_job_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG

# x86_uncore_perf publish on real stats.c rows (test_x86_uncore_perf stubs the setters).
test_fault_inject_x86_uncore_SOURCES = \
	test_fault_inject_x86_uncore.c \
	test_site_coverage.c \
	$(top_srcdir)/src/x86_uncore_perf.c \
	$(top_srcdir)/src/perf_util.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_fault_inject_x86_uncore_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_x86_uncore_LDADD = $(LDADD)

test_fault_inject_fs_SOURCES = \
	test_fault_inject_fs.c \
	test_site_coverage.c \
	$(top_srcdir)/src/proc.c \
	$(top_srcdir)/src/proc_status.c \
	$(top_srcdir)/src/proc_filter.c \
	$(top_srcdir)/src/host_key_alias.c \
	$(top_srcdir)/src/net.c \
	$(top_srcdir)/src/tmpfs.c \
	$(top_srcdir)/src/beegfs.c \
	$(top_srcdir)/src/beegfs_mount.c \
	$(top_srcdir)/src/beegfs_tcp.c \
	$(top_srcdir)/src/llite.c \
	$(top_srcdir)/src/lustre_proc_stats.c \
	$(top_srcdir)/src/lustre_obd_to_mnt.c \
	$(top_srcdir)/src/amd_gpu.c \
	$(top_srcdir)/src/pscanf.c \
	$(top_srcdir)/src/path_read.c \
	$(top_srcdir)/src/path_open_fail_once.c \
	$(top_srcdir)/src/sys_iter.c \
	$(top_srcdir)/src/stats_sentinel_debug.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_fault_inject_fs_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_fs_LDADD = $(LDADD) -ldl

test_fault_inject_cpu_SOURCES = \
	test_fault_inject_cpu.c \
	test_site_coverage.c \
	$(top_srcdir)/src/cpu_counter_metrics.c \
	$(top_srcdir)/src/cpu_counter_metrics_arm_util.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c
test_fault_inject_cpu_CPPFLAGS = $(AM_CPPFLAGS) -DDEBUG
test_fault_inject_cpu_LDADD = $(LDADD) -lm

test_stats_set_missing_SOURCES = \
	test_stats_set_missing.c \
	$(top_srcdir)/src/metric_profiler.c \
	$(top_srcdir)/src/stats.c \
	$(top_srcdir)/src/collect_tier.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/dict.c

test_dcgm_watch_policy_SOURCES = \
	test_dcgm_watch_policy.c \
	$(top_srcdir)/src/dcgm_watch_policy.c

test_stats_type_get_SOURCES = \
	test_stats_type_get.c \
	$(top_srcdir)/src/metric_profiler.c \
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
	$(top_srcdir)/src/stats_buffer_rmq_policy.c \
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
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c
test_stats_buffer_debug_shm_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK

test_debug_shm_emit_golden_SOURCES = \
	test_debug_shm_emit_golden.c \
	test_debug_shm_emit_fixture.c \
	test_debug_shm_emit_validate.c \
	$(stats_buffer_test_core_sources) \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c
test_debug_shm_emit_golden_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK -DSTATS_BUFFER_TEST_TIME_HOOK -DSTATS_BUFFER_TEST_UTS_HOOK

test_debug_shm_schema_mirror_SOURCES = \
	test_debug_shm_schema_mirror.c \
	$(top_srcdir)/src/stats_buffer_debug_shm.c \
	$(top_srcdir)/src/secure_path.c \
	$(top_srcdir)/src/monitor_log.c

test_monitor_timing_SOURCES = \
	test_monitor_timing.c \
	$(top_srcdir)/src/monitor_timing.c

# Link flags/libs match the configured tree (LDFLAGS/LIBS from configure).
test_rapl_powercap_LDADD = $(LDADD)
test_dict_LDADD = $(LDADD)
test_stats_sentinel_catalog_LDADD = $(LDADD)
test_host_key_alias_LDADD = $(LDADD)
test_proc_status_order_LDADD = $(LDADD)
test_schema_emit_LDADD = $(LDADD)
test_procfile_kv_LDADD = $(LDADD)
test_collect_str_LDADD = $(LDADD)
test_cpu_counter_arm_util_LDADD = $(LDADD)
test_cpu_counter_arm_publish_LDADD = $(LDADD)
test_perf_util_LDADD = $(LDADD)
test_arm_core_perf_LDADD = $(LDADD)
test_cpu_counter_x86_publish_LDADD = $(LDADD)
test_x86_core_perf_LDADD = $(LDADD)
test_x86_core_perf_bridge_LDADD = $(LDADD)
test_x86_uncore_perf_LDADD = $(LDADD)
test_x86_rapl_LDADD = $(LDADD) -lm
test_arm_power_hwmon_LDADD = $(LDADD)
test_arm_power_socket_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)

test_secure_path_SOURCES = \
	test_secure_path.c \
	$(top_srcdir)/src/secure_path.c
test_secure_path_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/src
test_dyn_lib_path_SOURCES = \
	test_dyn_lib_path.c \
	$(top_srcdir)/src/dyn_lib_path.c
test_dyn_lib_path_CPPFLAGS = $(AM_CPPFLAGS) -I$(top_srcdir)/src
test_secure_path_LDADD = $(LDADD)
test_dyn_lib_path_LDADD = $(LDADD)
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
test_stats_text_format_LDADD = $(LDADD)
test_gpu_schema_contract_LDADD = $(LDADD)
test_cpu_counter_metrics_schema_LDADD = $(LDADD)
test_cpu_counter_metrics_schema_arm_LDADD = $(LDADD)
test_roofline_hw_peak_schema_LDADD = $(LDADD)
test_roofline_hw_peak_changeover_LDADD = $(LDADD) -ldl -lm
test_roofline_detect_fixture_LDADD = $(LDADD) -ldl -lm
test_roofline_hw_peak_identity_LDADD = $(LDADD) -lm
test_nfs_schema_subset_LDADD = $(LDADD)
test_nfs_mountstats_parse_LDADD = $(LDADD)
test_ib_schema_contract_LDADD = $(LDADD)
test_host_schema_contract_LDADD = $(LDADD)
test_lustre_schema_contract_LDADD = $(LDADD)
test_beegfs_schema_contract_LDADD = $(LDADD)
test_beegfs_mount_LDADD = $(LDADD)
test_beegfs_tcp_LDADD = $(LDADD)
test_lustre_proc_stats_LDADD = $(LDADD)
test_opa_schema_contract_LDADD = $(LDADD)
test_opa_sysfs_map_LDADD = $(LDADD)
test_opa_mad_backoff_LDADD = $(LDADD)
test_ib_hca_is_opa_hfi_LDADD = $(LDADD)
test_opa_lspci_match_LDADD = $(LDADD)
test_lnet_schema_contract_LDADD = $(LDADD)
test_arm_aarch64_imc_schema_LDADD = $(LDADD)
test_arm_imc_scf_LDADD = $(LDADD)
test_amd_cpuid_match_LDADD = $(LDADD)
test_amd_x86_uncore_df_schema_LDADD = $(LDADD)
test_metric_profiler_LDADD = $(LDADD)
test_hwdetect_policy_LDADD = $(LDADD)
test_hwdetect_lspci_LDADD = $(LDADD)
test_gpu_pci_detect_LDADD = $(LDADD)
test_dcgm_gpu_dyn_LDADD = $(LDADD) -ldl
test_dcgm_session_LDADD = $(LDADD) -ldl
test_intel_gpu_schema_contract_LDADD = $(LDADD)
test_intel_gpu_publish_LDADD = $(LDADD) -ldl
test_intel_gpu_xpumcli_parse_LDADD = $(LDADD)
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
test_stats_runtime_begin_order_LDADD = $(LDADD) -lm
test_stats_runtime_collect_LDADD = $(LDADD) -lm
test_nvidia_gpu_estimate_LDADD = $(LDADD) -lm
test_nvidia_gpu_dcgm_field_LDADD = $(LDADD)
test_nvidia_gpu_dcgm_profiles_LDADD = $(LDADD)
test_daemonize_LDADD = $(LDADD)
test_collect_tier_table_LDADD = $(LDADD)
test_collect_phase_LDADD = $(LDADD) -lm
test_collect_tier_gate_LDADD = $(LDADD)
test_stats_set_tier_gate_LDADD = $(LDADD)
test_stats_set_missing_LDADD = $(LDADD)
test_collect_sentinels_LDADD = $(LDADD)
test_ib_sentinels_LDADD = $(LDADD)
test_gpu_power_sentinels_LDADD = $(LDADD)
test_debug_shm_sentinels_LDADD = $(LDADD)
test_fault_inject_matrix_LDADD = $(LDADD)
test_proc_prune_vanished_LDADD = $(LDADD)
test_lustre_osc_shared_sum_LDADD = $(LDADD)
test_lustre_zero_fill_LDADD = $(LDADD)
test_beegfs_io_LDADD = $(LDADD)
test_lustre_llite_paths_LDADD = $(LDADD)
test_stats_type_get_LDADD = $(LDADD)
test_dcgm_watch_policy_LDADD = $(LDADD) -lm
test_monitor_cli_LDADD = $(LDADD)
test_ring_buffer_LDADD = $(LDADD)
test_stats_buffer_collect_LDADD = $(LDADD)
test_stats_buffer_debug_shm_LDADD = $(LDADD)
test_debug_shm_emit_golden_LDADD = $(LDADD)
test_debug_shm_schema_mirror_LDADD = $(LDADD)
test_monitor_timing_LDADD = $(LDADD) -lm
