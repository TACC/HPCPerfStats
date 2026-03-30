# Monitor unit tests (see tests/README.md). Sources under $(top_srcdir)/src/ are
# production code linked into small check_PROGRAMS drivers.
AUTOMAKE_OPTIONS = subdir-objects

STATS_DIR_PATH = /var/log/hpcperfstats
STATS_LOCK_PATH = /var/lock/hpcperfstats
JOBID_FILE_PATH = /var/run/stats_jobid

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
monitor_unit_cppflags += -DRABBITMQ
endif

AM_CPPFLAGS = -I$(top_srcdir)/src $(monitor_unit_cppflags)

check_PROGRAMS = test_likwid_rapl_scale test_string1 test_stats_buffer_data_append test_path_collect test_pscanf test_schema_parse test_stats_file_format
if RABBITMQ
check_PROGRAMS += test_monitor_cli
endif

TESTS = $(check_PROGRAMS) test_monitor_configure_help.sh

EXTRA_DIST = test_monitor_configure_help.sh.in run_tests.sh README.md

test_likwid_rapl_scale_SOURCES = test_likwid_rapl_scale.c

test_string1_SOURCES = test_string1.c

test_stats_buffer_data_append_SOURCES = \
	test_stats_buffer_data_append.c \
	$(top_srcdir)/src/stats_buffer_data_append.c

test_path_collect_SOURCES = \
	test_path_collect.c \
	$(top_srcdir)/src/collect.c

test_pscanf_SOURCES = \
	test_pscanf.c \
	$(top_srcdir)/src/pscanf.c

test_schema_parse_SOURCES = \
	test_schema_parse.c \
	$(top_srcdir)/src/schema_entry_parse.c

test_stats_file_format_SOURCES = \
	test_stats_file_format.c \
	$(top_srcdir)/src/stats_file_format.c

if RABBITMQ
test_monitor_cli_SOURCES = \
	test_monitor_cli.c \
	test_monitor_cli_globals.c \
	$(top_srcdir)/src/monitor_cli.c
endif

# Link flags/libs match the configured tree (LDFLAGS/LIBS from configure).
test_likwid_rapl_scale_LDADD = $(LDADD)
test_string1_LDADD = $(LDADD)
test_stats_buffer_data_append_LDADD = $(LDADD)
test_path_collect_LDADD = $(LDADD)
test_pscanf_LDADD = $(LDADD)
test_schema_parse_LDADD = $(LDADD)
test_stats_file_format_LDADD = $(LDADD)
if RABBITMQ
test_monitor_cli_LDADD = $(LDADD)
endif
