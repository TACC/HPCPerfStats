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

AM_CPPFLAGS = -I$(top_srcdir)/src $(monitor_unit_cppflags)

check_PROGRAMS = test_likwid_rapl_scale test_string1 test_stats_buffer_data_append test_path_collect test_path_open_fail_once test_path_read test_sys_iter test_procfile_parse test_msr_io test_intel_mmconfig test_monitor_log test_pscanf test_schema_parse test_stats_file_format test_stats_text_format test_gpu_schema_contract test_cpu_counter_metrics_schema test_roofline_hw_peak_schema test_roofline_hw_peak_changeover test_dcgm_pkg_uniq test_nfs_schema_subset test_arm_aarch64_imc_schema test_amd_x86_pmc_schema test_metric_profiler test_hwdetect_policy test_stats_runtime_type_controls
if RABBITMQ
check_PROGRAMS += test_monitor_cli test_ring_buffer test_monitor_timing
endif

TESTS = $(check_PROGRAMS)

# Run from source (not generated): avoids Automake distdir-am needing a built .sh on some versions.
check-local:
	$(SHELL) '$(srcdir)/test_monitor_configure_help.sh.in'
	python3 '$(top_srcdir)/scripts/check_emitted_variable_names.py'
	$(SHELL) '$(top_srcdir)/scripts/check_unsafe_c_patterns.sh'
if RABBITMQ
	@if test "x$${RUN_RMQ_INTEGRATION:-0}" = "x1"; then \
	  $(MAKE) check-rabbitmq-integration; \
	fi
endif

EXTRA_DIST = test_monitor_configure_help.sh.in run_tests.sh README.md test_ring_buffer.c test_ring_buffer_stubs.c test_path_open_fail_once.c test_monitor_timing.c test_stats_text_format.c test_arm_aarch64_imc_schema.c test_amd_x86_pmc_schema.c test_rabbitmq_integration.sh rmq_integration_validate.py requirements-rabbitmq-integration.txt scripts/bootstrap_local_rabbitmq.sh scripts/check_unsafe_c_patterns.sh scripts/check_unsafe_c_patterns.allowlist

if RABBITMQ
.PHONY: check-rabbitmq-integration
check-rabbitmq-integration:
	$(SHELL) '$(srcdir)/test_rabbitmq_integration.sh' '$(top_builddir)'
endif

test_likwid_rapl_scale_SOURCES = test_likwid_rapl_scale.c

test_string1_SOURCES = test_string1.c

test_stats_buffer_data_append_SOURCES = \
	test_stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_buffer_data_append.c

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

test_stats_text_format_SOURCES = \
	test_stats_text_format.c \
	$(top_srcdir)/src/stats_text_format.c

test_gpu_schema_contract_SOURCES = \
	test_gpu_schema_contract.c

test_dcgm_pkg_uniq_SOURCES = test_dcgm_pkg_uniq.c

test_cpu_counter_metrics_schema_SOURCES = test_cpu_counter_metrics_schema.c

test_roofline_hw_peak_schema_SOURCES = \
	test_roofline_hw_peak_schema.c

test_roofline_hw_peak_changeover_SOURCES = \
	test_roofline_hw_peak_changeover.c \
	$(top_srcdir)/src/roofline_hw_peak.c \
	$(top_srcdir)/src/roofline_hw_peak_detect.c

test_nfs_schema_subset_SOURCES = \
	test_nfs_schema_subset.c \
	$(top_srcdir)/src/nfs.c

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

test_stats_runtime_type_controls_SOURCES = \
	test_stats_runtime_type_controls.c \
	$(top_srcdir)/src/stats_runtime.c

if RABBITMQ
test_monitor_cli_SOURCES = \
	test_monitor_cli.c \
	test_monitor_cli_globals.c \
	$(top_srcdir)/src/monitor_cli.c \
	$(top_srcdir)/src/monitor_options.c \
	$(top_srcdir)/src/monitor_log.c

test_ring_buffer_SOURCES = \
	test_ring_buffer.c \
	test_ring_buffer_stubs.c \
	$(top_srcdir)/src/stats_buffer.c \
	$(top_srcdir)/src/stats_buffer_rmq.c \
	$(top_srcdir)/src/stats_buffer_rows.c \
	$(top_srcdir)/src/stats_buffer_uts.c \
	$(top_srcdir)/src/stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_text_format.c \
	$(top_srcdir)/src/monitor_log.c \
	$(top_srcdir)/src/path_open_fail_once.c
test_ring_buffer_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK

test_monitor_timing_SOURCES = \
	test_monitor_timing.c \
	$(top_srcdir)/src/monitor_timing.c
endif

# Link flags/libs match the configured tree (LDFLAGS/LIBS from configure).
test_likwid_rapl_scale_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)
test_stats_buffer_data_append_LDADD = $(LDADD)
test_path_collect_LDADD = $(LDADD)
test_path_open_fail_once_LDADD = $(LDADD)
test_path_read_LDADD = $(LDADD)
test_sys_iter_LDADD = $(LDADD)
test_procfile_parse_LDADD = $(LDADD)
test_msr_io_LDADD = $(LDADD)
test_intel_mmconfig_LDADD = $(LDADD)
test_monitor_log_LDADD = $(LDADD)
test_pscanf_LDADD = $(LDADD)
test_schema_parse_LDADD = $(LDADD)
test_stats_file_format_LDADD = $(LDADD)
test_stats_text_format_LDADD = $(LDADD)
test_gpu_schema_contract_LDADD = $(LDADD)
test_dcgm_pkg_uniq_LDADD = $(LDADD)
test_cpu_counter_metrics_schema_LDADD = $(LDADD)
test_roofline_hw_peak_schema_LDADD = $(LDADD)
test_roofline_hw_peak_changeover_LDADD = $(LDADD)
test_nfs_schema_subset_LDADD = $(LDADD)
test_arm_aarch64_imc_schema_LDADD = $(LDADD)
test_amd_x86_pmc_schema_LDADD = $(LDADD)
test_metric_profiler_LDADD = $(LDADD)
test_hwdetect_policy_LDADD = $(LDADD)
test_stats_runtime_type_controls_LDADD = $(LDADD)
if RABBITMQ
test_monitor_cli_LDADD = $(LDADD)
test_ring_buffer_LDADD = $(LDADD)
test_monitor_timing_LDADD = $(LDADD) -lm
endif
