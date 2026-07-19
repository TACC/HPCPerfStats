ACLOCAL_AMFLAGS = -I m4

# Compile monitor C sources as C.

AM_DISTCHECK_CONFIGURE_FLAGS = \
  --with-systemduserunitdir=$$dc_install_base/$(systemduserunitdir)

if HAVE_SYSTEMD
systemduserunit_DATA = \
	hpcperfstats.service \
	hpcperfstats.conf \
	hpcperfstatsd.sysconfig
endif

bin_PROGRAMS = hpcperfstatsd

STATS_DIR_PATH = /var/log/hpcperfstats
STATS_LOCK_PATH = /var/lock/hpcperfstats
JOBID_FILE_PATH = /var/run/stats_jobid

# rabbitmq-c 0.9+ handshake/RPC timeout APIs (stats_buffer.c). Empty this when linking pre-0.9 headers.
MONITOR_AMQP_TIMEOUT_CPPFLAGS = \
	-DHAVE_AMQP_SET_RPC_TIMEOUT=1 \
	-DHAVE_AMQP_SET_HANDSHAKE_TIMEOUT=1

hpcperfstatsd_CPPFLAGS = \
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
	collect_tier.h \
	collect_tier.c \
	cpuid.h \
	cpuid.c \
	intel_cpuid_match.h \
	intel_cpuid_match.c \
	intel_processor.h \
	intel_processor.c \
	daemonize.h \
	daemonize.c \
	monitor_log.h \
	monitor_log.c \
	dict.h \
	dict.c \
	path_open_fail_once.h \
	path_open_fail_once.c \
	path_read.h \
	path_read.c \
	sys_iter.h \
	sys_iter.c \
	procfile_parse.h \
	procfile_parse.c \
	procfile_kv.h \
	msr_io.h \
	msr_io.c \
	intel_mmconfig.h \
	intel_mmconfig.c \
	fileio.h \
	hwdetect.h \
	hwdetect.c \
	gpu_pci_detect.h \
	gpu_pci_detect.c \
	host_key_alias.h \
	host_key_alias.c \
	host_cpu.h \
	host_mem.h \
	host_net.h \
	host_ps.h \
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
	stats_registry.h \
	stats_registry.c \
	metric_profiler.h \
	metric_profiler.c \
	roofline_hw_peak.h \
	string1.h \
	trace.h \
	stats_sink.h \
	stats_runtime.h \
	stats_runtime.c \
	stats_text_format.h \
	stats_text_format.c

if METRIC_PROFILER_BACKEND_EBPF
hpcperfstatsd_SOURCES += \
	metric_profiler_ebpf.h \
	metric_profiler_ebpf.c
endif

if RABBITMQ
hpcperfstatsd_SOURCES += \
	monitor.c \
	monitor_cli.c \
	monitor_cli.h \
	monitor_options.h \
	monitor_options.c \
	monitor_daemon.c \
	monitor_daemon.h \
	monitor_timing.c \
	monitor_timing.h \
	stats_buffer_data_append.c \
	stats_buffer_data_append.h \
	stats_buffer.c \
	stats_buffer.h \
	stats_buffer_debug_shm.c \
	stats_buffer_debug_shm.h \
	stats_buffer_rmq.c \
	stats_buffer_rmq_internal.h \
	stats_buffer_rows.c \
	stats_buffer_rows.h \
	stats_buffer_uts.c \
	stats_buffer_uts.h
hpcperfstatsd_CPPFLAGS += \
	-DRABBITMQ \
	$(MONITOR_AMQP_TIMEOUT_CPPFLAGS)
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
	ib_common.c \
	ib_port_state.c \
	ib_family.c \
	lnet.c \
	mem.c \
	net.c \
	nfs.c \
	numa.c \
	roofline_hw_peak.c \
	roofline_hw_peak_detect.c \
	roofline_hw_peak_detect.h \
	host_edac_mem_topology.c \
	host_edac_mem_topology.h \
	proc.c \
	ps.c \
	sysv_shm.c \
	tmpfs.c \
	vfs.c \
	vm.c

if HARDWARE
hpcperfstatsd_SOURCES += \
	cpu_counter_metrics.h
if CPU_BACKEND_LIKWID
TYPES 	+= \
	cpu_counter_metrics.c \
	cpu_counter_metrics_dcgm_publish.c \
	cpu_counter_metrics_dcgm_publish.h \
	cpu_counter_metrics_dcgm_state.h \
	cpu_counter_metrics_likwid_begin.c \
	cpu_counter_metrics_likwid_begin.h \
	amd64_rapl.c \
	intel_rapl.c \
	amd64_pmc.c \
	amd64_df.c \
	intel_4pmc3.c \
	intel_8pmc3.c \
	intel_pmc3_core.c \
	intel_uncore_msr_box.c \
	intel_uncore_pci.c \
	intel_icx_imc.c \
	intel_spr_imc.c \
	intel_skx_cha.c \
	intel_skx_imc.c
hpcperfstatsd_SOURCES += \
	amd64_event_tables.h \
	amd64_event_tables.c \
	amd64_pmu_core.h \
	amd64_pmu_core.c \
	cpu_counter_metrics.h \
	intel_pmc3_core.h \
	intel_topology_walk.h \
	intel_uncore_mmio.h \
	intel_uncore_mmio.c \
	intel_uncore_msr_box.h \
	intel_uncore_pci.h \
	rapl_likwid_stats.h \
	rapl_likwid_stats.c
endif
if CPU_BACKEND_DCGM
TYPES += \
	cpu_counter_metrics.c \
	cpu_counter_metrics_dcgm_publish.c \
	cpu_counter_metrics_dcgm_publish.h \
	cpu_counter_metrics_dcgm_state.h \
	cpu_counter_metrics_dcgm_util.c \
	cpu_counter_metrics_dcgm_util.h
if MONITOR_ARCH_ARM
TYPES += \
	arm_imc.c
hpcperfstatsd_SOURCES += \
	arm_imc.h
endif
endif
endif

if LIKWID
hpcperfstatsd_SOURCES += \
	likwid_rapl.h \
	likwid_rapl.c \
	likwid_pmc_adapter.h \
	likwid_pmc_adapter.c \
	likwid_arch_map.h \
	likwid_arch_map.c \
	likwid_uncore_adapter.h \
	likwid_uncore_adapter.c \
	likwid_uncore_profiles.h \
	likwid_uncore_profiles.c \
	intel_skx_imc.h \
	intel_icx_imc.h \
	intel_spr_imc.h
endif

if INFINIBAND
TYPES	 += \
	ib_mad.c
endif

hpcperfstatsd_LDFLAGS =
# host_opa always built (sysfs); STL MAD path requires --enable-opa + liboib_utils.
TYPES += \
	opa.c \
	opa_sysfs.c \
	opa_mad_backoff.c
if OPA
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

hpcperfstatsd_SOURCES += \
	ib.h \
	ib_common.h \
	ib_port_state.h \
	ib_family.h \
	ib_mad.h \
	host_opa.h \
	opa_sysfs.h \
	opa_mad_backoff.h \
	$(TYPES)

hpcperfstatsd_LDFLAGS += \
	-lm

if GPU
TYPES += \
	nvidia_gpu.c \
	nvidia_gpu_dcgm_watch.c \
	nvidia_gpu_dcgm_watch.h \
	nvidia_gpu_dcgm_compat.h
if GPU_DLOPEN
hpcperfstatsd_SOURCES += \
	dcgm_gpu_dyn.c \
	dcgm_gpu_dyn.h \
	dcgm_gpu_api.h
endif
hpcperfstatsd_SOURCES += \
	nvidia_gpu.h \
	nvidia_gpu_estimate.c \
	nvidia_gpu_estimate.h
endif

if AMD_GPU
TYPES += \
	amd_gpu.c
hpcperfstatsd_SOURCES += \
	amd_gpu.h
endif

if INTEL_GPU
TYPES += \
	intel_gpu.c
hpcperfstatsd_SOURCES += \
	intel_gpu.h \
	xpum_gpu_dyn.c \
	xpum_gpu_dyn.h
hpcperfstatsd_CPPFLAGS += \
	-I$(top_srcdir)/third_party/intel-xpum
endif

EXTRA_DIST = hpcperfstats.conf hpcperfstats.service hpcperfstatsd.sysconfig \
	$(top_srcdir)/scripts/build_static_bundle.sh \
	$(top_srcdir)/scripts/ensure_dotbuild_prefix_static.sh \
	$(top_srcdir)/scripts/lib/monitor_tree_clean.sh
