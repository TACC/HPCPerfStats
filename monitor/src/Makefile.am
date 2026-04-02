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

if MONITOR_USE_DCGM_VENDOR_INCLUDES
hpcperfstatsd_CPPFLAGS += \
	-I$(top_srcdir)/third_party/nvidia-dcgm
endif

if DEBUG
hpcperfstatsd_CPPFLAGS += \
	-DDEBUG
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
	path_open_fail_once.h \
	path_open_fail_once.c \
	fileio.h \
	hwdetect.h \
	hwdetect.c \
	intel_pmc3.h \
	intel_pmc_uncore.h \
	JOIN.h \
	pci.h \
	pci.c \
	pscanf.c \
	pscanf.h \
	schema.h \
	schema.c \
	schema_entry_parse.c \
	stats.h \
	stats.c \
	string1.h \
	trace.h

if RABBITMQ
hpcperfstatsd_SOURCES += \
	monitor.c \
	monitor_cli.c \
	monitor_cli.h \
	monitor_daemon.c \
	monitor_daemon.h \
	stats_buffer_data_append.c \
	stats_buffer_data_append.h \
	stats_buffer.c \
	stats_buffer.h
hpcperfstatsd_CPPFLAGS += \
	-DRABBITMQ
else
hpcperfstatsd_SOURCES += \
	main.c \
	stats_file_format.c \
	stats_file_format.h \
	stats_file.c \
	stats_file.h
endif

if MONITOR_USE_DCGM_VENDOR_INCLUDES
hpcperfstatsd_SOURCES += \
	dcgm_session.c \
	dcgm_session.h
endif

TYPES 	= \
	block.c \
	cpu.c \
	ib.c \
	lnet.c \
	mem.c \
	net.c \
	nfs.c \
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
	cpu_counter_metrics.c \
	amd64_rapl.c \
	intel_rapl.c \
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
hpcperfstatsd_SOURCES += \
	cpu_counter_metrics.h
endif
if CPU_BACKEND_DCGM
TYPES += \
	cpu_counter_metrics.c
if MONITOR_ARCH_ARM
TYPES += \
	arm_imc.c
hpcperfstatsd_SOURCES += \
	arm_imc.h
endif
hpcperfstatsd_SOURCES += \
	cpu_counter_metrics.h
endif
endif

if LIKWID
hpcperfstatsd_SOURCES += \
	likwid_rapl.h \
	likwid_rapl.c \
	likwid_pmc_adapter.h \
	likwid_pmc_adapter.c \
	likwid_arch_map.h \
	likwid_arch_map.c
endif

if INFINIBAND
TYPES	 += \
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
	llite.c
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
	$(top_srcdir)/scripts/ensure_dotbuild_prefix_static.sh
