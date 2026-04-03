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
	@CPU_COUNTER_BACKEND_CPPFLAG@

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

check_PROGRAMS = test_likwid_rapl_scale test_string1 test_stats_buffer_data_append test_path_collect test_path_open_fail_once test_pscanf test_schema_parse test_stats_file_format test_gpu_schema_contract test_cpu_counter_metrics_schema test_dcgm_pkg_uniq test_nfs_schema_subset
if RABBITMQ
check_PROGRAMS += test_monitor_cli test_ring_buffer
endif

TESTS = $(check_PROGRAMS)

# Run from source (not generated): avoids Automake distdir-am needing a built .sh on some versions.
check-local:
	$(SHELL) '$(srcdir)/test_monitor_configure_help.sh.in'

EXTRA_DIST = test_monitor_configure_help.sh.in run_tests.sh README.md test_ring_buffer.c test_ring_buffer_stubs.c test_path_open_fail_once.c

test_likwid_rapl_scale_SOURCES = test_likwid_rapl_scale.c

test_string1_SOURCES = test_string1.c

test_stats_buffer_data_append_SOURCES = \
	test_stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_buffer_data_append.c

test_path_collect_SOURCES = \
	test_path_collect.c \
	$(top_srcdir)/src/collect.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_path_open_fail_once_SOURCES = \
	test_path_open_fail_once.c \
	$(top_srcdir)/src/path_open_fail_once.c

test_pscanf_SOURCES = \
	test_pscanf.c \
	$(top_srcdir)/src/pscanf.c

test_schema_parse_SOURCES = \
	test_schema_parse.c \
	$(top_srcdir)/src/schema_entry_parse.c \
	$(top_srcdir)/src/schema.c \
	$(top_srcdir)/src/dict.c

test_stats_file_format_SOURCES = \
	test_stats_file_format.c \
	$(top_srcdir)/src/stats_file_format.c

test_gpu_schema_contract_SOURCES = \
	test_gpu_schema_contract.c

test_dcgm_pkg_uniq_SOURCES = test_dcgm_pkg_uniq.c

test_cpu_counter_metrics_schema_SOURCES = test_cpu_counter_metrics_schema.c

test_nfs_schema_subset_SOURCES = \
	test_nfs_schema_subset.c \
	$(top_srcdir)/src/nfs.c

if RABBITMQ
test_monitor_cli_SOURCES = \
	test_monitor_cli.c \
	test_monitor_cli_globals.c \
	$(top_srcdir)/src/monitor_cli.c

test_ring_buffer_SOURCES = \
	test_ring_buffer.c \
	test_ring_buffer_stubs.c \
	$(top_srcdir)/src/stats_buffer.c \
	$(top_srcdir)/src/stats_buffer_data_append.c \
	$(top_srcdir)/src/path_open_fail_once.c
test_ring_buffer_CPPFLAGS = $(AM_CPPFLAGS) -DSTATS_BUFFER_TEST_SEND_HOOK
endif

# Link flags/libs match the configured tree (LDFLAGS/LIBS from configure).
test_likwid_rapl_scale_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)
test_stats_buffer_data_append_LDADD = $(LDADD)
test_path_collect_LDADD = $(LDADD)
test_path_open_fail_once_LDADD = $(LDADD)
test_pscanf_LDADD = $(LDADD)
test_schema_parse_LDADD = $(LDADD)
test_stats_file_format_LDADD = $(LDADD)
test_gpu_schema_contract_LDADD = $(LDADD)
test_dcgm_pkg_uniq_LDADD = $(LDADD)
test_cpu_counter_metrics_schema_LDADD = $(LDADD)
test_nfs_schema_subset_LDADD = $(LDADD)
if RABBITMQ
test_monitor_cli_LDADD = $(LDADD)
test_ring_buffer_LDADD = $(LDADD)
endif
