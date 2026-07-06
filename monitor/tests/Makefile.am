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

check_PROGRAMS = test_cpuid_signature test_likwid_rapl_scale test_likwid_uncore_profiles test_likwid_uncore_adapter test_intel_skx_imc_schema test_intel_icx_imc_schema test_intel_spr_imc_schema test_string1 test_stats_buffer_data_append test_stats_buffer_uts test_stats_buffer_rows test_path_collect test_path_open_fail_once test_path_read test_sys_iter test_procfile_parse test_msr_io test_intel_mmconfig test_monitor_log test_monitor_options_kv test_pscanf test_schema_parse test_stats_file_format test_stats_file_format_extra test_stats_text_format test_gpu_schema_contract test_cpu_counter_metrics_schema test_roofline_hw_peak_schema test_roofline_hw_peak_changeover test_roofline_detect_fixture test_nfs_schema_subset test_ib_schema_contract test_host_schema_contract test_lustre_schema_contract test_opa_schema_contract test_lnet_schema_contract test_arm_aarch64_imc_schema test_amd_x86_pmc_schema test_metric_profiler test_hwdetect_policy test_hwdetect_lspci test_ib_port_state test_ib_port_collectible test_ib_foreach_hca_port test_ib_family test_stats_runtime_type_controls test_stats_runtime_collect test_collect_tier_table test_collect_phase test_collect_tier_gate test_stats_set_tier_gate test_dict test_host_key_alias test_schema_emit test_procfile_kv test_collect_str test_nvidia_gpu_estimate test_daemonize
if INFINIBAND
check_PROGRAMS += test_ib_mad_backoff test_ib_mad_decode test_ib_mad_sw_swap
endif
if CPU_BACKEND_DCGM
check_PROGRAMS += test_dcgm_pkg_uniq test_cpu_counter_dcgm_util test_cpu_counter_dcgm_publish
endif
if RABBITMQ
check_PROGRAMS += test_monitor_cli test_ring_buffer test_stats_buffer_collect test_stats_buffer_debug_shm test_debug_shm_emit_golden test_debug_shm_schema_mirror test_monitor_timing
endif

TESTS = $(check_PROGRAMS)

# Run from source (not generated): avoids Automake distdir-am needing a built .sh on some versions.
check-local:
	$(SHELL) '$(srcdir)/test_monitor_configure_help.sh.in'
	$(SHELL) '$(srcdir)/test_static_bundle_configure_flags.sh'
	$(SHELL) '$(srcdir)/test_monitor_configure_amd_auto.sh' || test $$? -eq 77
	python3 '$(top_srcdir)/scripts/check_emitted_variable_names.py'
	$(SHELL) '$(top_srcdir)/scripts/check_unsafe_c_patterns.sh'
	@$(SHELL) '$(srcdir)/test_shm_message_correctness.sh' '$(top_builddir)' || test $$? -eq 77
if RABBITMQ
	@if test "x$${RUN_RMQ_INTEGRATION:-0}" = "x1"; then \
	  $(MAKE) check-rabbitmq-integration; \
	fi
endif

EXTRA_DIST = test_monitor_configure_help.sh.in test_static_bundle_configure_flags.sh test_monitor_configure_amd_auto.sh run_tests.sh README.md test_ring_buffer.c test_ring_buffer_stubs.c test_stats_buffer_collect.c test_stats_buffer_collect_stubs.c test_stats_buffer_collect_stubs.h test_debug_shm_emit_fixture.c test_debug_shm_emit_fixture.h test_debug_shm_emit_validate.c test_debug_shm_emit_validate.h test_debug_shm_emit_golden.c test_debug_shm_schema_mirror.c test_shm_message_correctness.sh expected/debug_shm_fast.txt expected/debug_shm_full.txt expected/synthetic_fixture/schema expected/synthetic_fixture/fast expected/synthetic_fixture/full expected/synthetic_fixture/monitor-build-capabilities.json expected/synthetic_fixture/expectations_synthetic_debug_tier1.json test_stats_buffer_rows.c test_stats_runtime_collect.c test_ib_family.c test_ib_mad_backoff.c test_ib_mad_decode.c test_ib_mad_stubs.c test_ib_mad_sw_swap.c test_nvidia_gpu_estimate.c test_roofline_detect_fixture.c test_daemonize.c test_path_open_fail_once.c test_monitor_timing.c test_stats_text_format.c test_arm_aarch64_imc_schema.c test_amd_x86_pmc_schema.c test_rabbitmq_integration.sh rmq_integration_validate.py requirements-rabbitmq-integration.txt scripts/bootstrap_local_rabbitmq.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.sh $(top_srcdir)/scripts/check_unsafe_c_patterns.allowlist $(top_srcdir)/scripts/check_emitted_variable_names.py $(top_srcdir)/scripts/emit_build_capabilities.py $(top_srcdir)/scripts/build_message_expectations.py $(top_srcdir)/scripts/validate_shm_messages.py $(top_srcdir)/scripts/lib/message_parse.py $(top_srcdir)/scripts/lib/row_validate.py $(top_srcdir)/scripts/lib/__init__.py

if RABBITMQ
.PHONY: check-rabbitmq-integration
check-rabbitmq-integration:
	$(SHELL) '$(srcdir)/test_rabbitmq_integration.sh' '$(top_builddir)'
endif

test_likwid_rapl_scale_SOURCES = test_likwid_rapl_scale.c
if LIKWID
test_likwid_rapl_scale_SOURCES += $(top_srcdir)/src/likwid_rapl.c
endif

test_cpuid_signature_SOURCES = \
	test_cpuid_signature.c \
	$(top_srcdir)/src/intel_cpuid_match.c

test_likwid_uncore_profiles_SOURCES = \
	test_likwid_uncore_profiles.c \
	$(top_srcdir)/src/likwid_uncore_profiles.c \
	$(top_srcdir)/src/intel_processor.c

test_likwid_uncore_adapter_SOURCES = \
	test_likwid_uncore_adapter.c \
	test_stats_stub.c \
	$(top_srcdir)/src/likwid_uncore_adapter.c \
	$(top_srcdir)/src/likwid_uncore_profiles.c \
	$(top_srcdir)/src/intel_processor.c

test_intel_skx_imc_schema_SOURCES = test_intel_skx_imc_schema.c
test_intel_icx_imc_schema_SOURCES = test_intel_icx_imc_schema.c
test_intel_spr_imc_schema_SOURCES = test_intel_spr_imc_schema.c

test_dict_SOURCES = \
	test_dict.c \
	$(top_srcdir)/src/dict.c

test_host_key_alias_SOURCES = \
	test_host_key_alias.c \
	test_stats_stub.c \
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

test_cpu_counter_dcgm_publish_SOURCES = \
	test_cpu_counter_dcgm_publish.c \
	test_stats_stub.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_publish.c \
	$(top_srcdir)/src/cpu_counter_metrics_dcgm_util.c

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

test_msr_io_SOURCES = \
	test_msr_io.c \
	$(top_srcdir)/src/msr_io.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_intel_mmconfig_SOURCES = \
	test_intel_mmconfig.c \
	$(top_srcdir)/src/intel_mmconfig.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_monitor_log_SOURCES = \
	test_monitor_log.c \
	$(top_srcdir)/src/monitor_log.c

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
	$(top_srcdir)/src/roofline_hw_peak_detect.c

test_roofline_detect_fixture_SOURCES = \
	test_roofline_detect_fixture.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c

test_nfs_schema_subset_SOURCES = \
	test_nfs_schema_subset.c \
	$(top_srcdir)/src/nfs.c

test_ib_schema_contract_SOURCES = test_ib_schema_contract.c

test_host_schema_contract_SOURCES = test_host_schema_contract.c

test_lustre_schema_contract_SOURCES = test_lustre_schema_contract.c

test_opa_schema_contract_SOURCES = test_opa_schema_contract.c

test_lnet_schema_contract_SOURCES = test_lnet_schema_contract.c

test_arm_aarch64_imc_schema_SOURCES = test_arm_aarch64_imc_schema.c

test_amd_x86_pmc_schema_SOURCES = test_amd_x86_pmc_schema.c

test_metric_profiler_SOURCES = \
	test_metric_profiler.c \
	$(top_srcdir)/src/metric_profiler.c
if METRIC_PROFILER_BACKEND_EBPF
test_metric_profiler_SOURCES += \
	$(top_srcdir)/src/metric_profiler_ebpf.c
endif

test_hwdetect_policy_SOURCES = \
	test_hwdetect_policy.c \
	$(top_srcdir)/src/hwdetect.c

test_hwdetect_lspci_SOURCES = \
	test_hwdetect_lspci.c \
	$(top_srcdir)/src/hwdetect.c

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
	$(top_srcdir)/src/ib_mad.c

test_ib_mad_decode_SOURCES = \
	test_ib_mad_decode.c \
	test_ib_mad_stubs.c \
	test_stats_stub.c \
	$(top_srcdir)/src/ib_mad.c

test_ib_mad_sw_swap_SOURCES = \
	test_ib_mad_sw_swap.c \
	test_ib_mad_stubs.c \
	test_stats_stub.c \
	$(top_srcdir)/src/ib_mad.c
endif

test_stats_runtime_type_controls_SOURCES = \
	test_stats_runtime_type_controls.c \
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
test_likwid_rapl_scale_LDADD = $(LDADD)
test_likwid_uncore_adapter_LDADD = $(LDADD)
if LIKWID
test_likwid_rapl_scale_LDADD += -lm
test_likwid_uncore_adapter_LDADD += -lm
endif
test_dict_LDADD = $(LDADD)
test_host_key_alias_LDADD = $(LDADD)
test_schema_emit_LDADD = $(LDADD)
test_procfile_kv_LDADD = $(LDADD)
test_collect_str_LDADD = $(LDADD)
test_cpu_counter_dcgm_util_LDADD = $(LDADD)
test_cpu_counter_dcgm_publish_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)
test_stats_buffer_data_append_LDADD = $(LDADD)
test_stats_buffer_uts_LDADD = $(LDADD)
test_stats_buffer_rows_LDADD = $(LDADD)
test_path_collect_LDADD = $(LDADD)
test_path_open_fail_once_LDADD = $(LDADD)
test_path_read_LDADD = $(LDADD)
test_sys_iter_LDADD = $(LDADD)
test_procfile_parse_LDADD = $(LDADD)
test_msr_io_LDADD = $(LDADD)
test_intel_mmconfig_LDADD = $(LDADD)
test_monitor_log_LDADD = $(LDADD)
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
test_opa_schema_contract_LDADD = $(LDADD)
test_lnet_schema_contract_LDADD = $(LDADD)
test_arm_aarch64_imc_schema_LDADD = $(LDADD)
test_amd_x86_pmc_schema_LDADD = $(LDADD)
test_metric_profiler_LDADD = $(LDADD)
test_hwdetect_policy_LDADD = $(LDADD)
test_hwdetect_lspci_LDADD = $(LDADD)
test_ib_port_state_LDADD = $(LDADD)
test_ib_port_collectible_LDADD = $(LDADD)
test_ib_foreach_hca_port_LDADD = $(LDADD)
test_ib_family_LDADD = $(LDADD)
if INFINIBAND
test_ib_mad_backoff_LDADD = $(LDADD)
test_ib_mad_decode_LDADD = $(LDADD)
test_ib_mad_sw_swap_LDADD = $(LDADD)
endif
test_stats_runtime_type_controls_LDADD = $(LDADD) -lm
test_stats_runtime_collect_LDADD = $(LDADD) -lm
test_nvidia_gpu_estimate_LDADD = $(LDADD) -lm
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
