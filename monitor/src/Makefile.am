ACLOCAL_AMFLAGS = -I m4

# Compile monitor C sources as C.

AM_DISTCHECK_CONFIGURE_FLAGS = \
  --with-systemduserunitdir=$$dc_install_base/$(systemduserunitdir)

if HAVE_SYSTEMD
systemduserunit_DATA = \
	hpcperfstats.service \
	hpcperfstats.conf
endif

bin_PROGRAMS = hpcperfstatsd

STATS_DIR_PATH = /var/log/hpcperfstats
STATS_LOCK_PATH = /var/lock/hpcperfstats
JOBID_FILE_PATH = /var/run/stats_jobid

hpcperfstatsd_CPPFLAGS = \
	-D_GNU_SOURCE \
	-DSTATS_PROGRAM=\"@PACKAGE@\" \
	-DSTATS_VERSION=\"@PACKAGE_VERSION@\" \
	-DSTATS_DIR_PATH=\"$(STATS_DIR_PATH)\" \
	-DSTATS_LOCK_PATH=\"$(STATS_LOCK_PATH)\" \
	-DJOBID_FILE_PATH=\"$(JOBID_FILE_PATH)\" \
	@CPU_COUNTER_BACKEND_CPPFLAG@

if DEBUG
hpcperfstatsd_CPPFLAGS += \
	-DDEBUG
endif

if VARIORUM
hpcperfstatsd_CPPFLAGS += \
	-DHAVE_VARIORUM
endif

if LIKWID
hpcperfstatsd_CPPFLAGS += \
	-DHAVE_LIKWID \
	@MONITOR_ARCH_CPPFLAG@
endif

hpcperfstatsd_SOURCES = \
	amd64_pmc.h \
	amd64_df.h \
	collect.h \
	collect.c \
	cpuid.h \
	cpuid.c \
	daemonize.h \
	daemonize.c \
	dict.h \
	dict.c \
	hwdetect.h \
	hwdetect.c \
	intel_pmc3.h \
	intel_pmc_uncore.h \
	JOIN.h \
	pci.h \
	pci.c \
	pscanf.h \
	schema.h \
	schema.c \
	stats.h \
	stats.c \
	string1.h \
	trace.h

if RABBITMQ
hpcperfstatsd_SOURCES += \
	monitor.c \
	stats_buffer.c \
	stats_buffer.h
hpcperfstatsd_CPPFLAGS += \
	-DRABBITMQ
else
hpcperfstatsd_SOURCES += \
	main.c \
	stats_file.c \
	stats_file.h
endif

TYPES 	= \
	block.c \
	cpu.c \
	mem.c \
	net.c \
	numa.c \
	proc.c \
	ps.c \
	sysv_shm.c \
	tmpfs.c \
	vfs.c \
	vm.c

if HARDWARE
if CPU_BACKEND_LIKWID
TYPES 	+= \
	amd64_rapl.c \
	intel_rapl.c \
	cpu_counter_metrics.c
TYPES 	+= \
	amd64_pmc.c \
	amd64_df.c \
	intel_4pmc3.c \
	intel_8pmc3.c \
	intel_bdw_cbo.c \
	intel_bdw_hau.c \
	intel_bdw_imc.c \
	intel_bdw_qpi.c \
	intel_bdw_r2pci.c \
	intel_hsw_cbo.c \
	intel_hsw_hau.c \
	intel_hsw_imc.c \
	intel_hsw_qpi.c \
	intel_hsw_r2pci.c \
	intel_ivb_cbo.c \
	intel_ivb_hau.c \
	intel_ivb_imc.c \
	intel_ivb_qpi.c \
	intel_ivb_r2pci.c \
	intel_knl.c \
	intel_knl_edc.c \
	intel_knl_mc.c \
	intel_pcu.c \
	intel_skx_cha.c \
	intel_skx_imc.c \
	intel_snb_cbo.c \
	intel_snb_hau.c \
	intel_snb_imc.c \
	intel_snb_qpi.c \
	intel_snb_r2pci.c
endif
if CPU_BACKEND_DCGM
TYPES += \
	cpu_counter_metrics.c
endif
endif

if VARIORUM
hpcperfstatsd_SOURCES += \
	variorum_rapl.h \
	variorum_rapl.c
endif

if LIKWID
hpcperfstatsd_SOURCES += \
	likwid_pmc_adapter.h \
	likwid_pmc_adapter.c \
	likwid_arch_map.h
if MONITOR_ARCH_INTEL
hpcperfstatsd_SOURCES += \
	likwid_arch_map_intel.c
endif
if MONITOR_ARCH_AMD
hpcperfstatsd_SOURCES += \
	likwid_arch_map_amd.c
endif
endif

if INFINIBAND
TYPES	 += \
	ib.c \
	ib_ext.c \
	ib_sw.c
endif

hpcperfstatsd_LDFLAGS =
if OPA
TYPES	+= \
	opa.c
hpcperfstatsd_LDFLAGS += \
		-lpthread \
		-lmemusage
endif

if LUSTRE
hpcperfstatsd_SOURCES += \
	lustre_obd_to_mnt.c \
	lustre_obd_to_mnt.h

TYPES += \
	mdc.c \
	llite.c \
	osc.c
endif

if MIC
hpcperfstatsd_SOURCES += \
	miclib.h 
TYPES += \
	mic.c
endif

hpcperfstatsd_SOURCES += \
	$(TYPES)

hpcperfstatsd_LDFLAGS += \
	-lm

nodist_hpcperfstatsd_SOURCES = \
	stats.x

if GPU
TYPES += \
	nvidia_gpu.c
hpcperfstatsd_SOURCES += \
	nvidia_gpu.h
endif

if AMD_GPU
TYPES += \
	amd_gpu.c
hpcperfstatsd_SOURCES += \
	amd_gpu.h
endif

BUILT_SOURCES = stats.x
CLEANFILES = stats.x
stats.o: stats.x
stats.x: Makefile
	echo '$(patsubst %.c, X(%),$(sort $(TYPES)))' > $@

EXTRA_DIST = hpcperfstats.conf hpcperfstats.service \
	$(top_srcdir)/scripts/build_static_bundle.sh \
	test_monitor_configure_help.sh

check_PROGRAMS = test_variorum_rapl_parser
TESTS = $(check_PROGRAMS) test_monitor_configure_help.sh
test_variorum_rapl_parser_SOURCES = \
	test_variorum_rapl_parser.c \
	variorum_rapl.c
