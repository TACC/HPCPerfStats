%global srcname hpcperfstats
%if 0%{?hpc_debug_build}
# Debug/profiling build: keep full symbols and emit debuginfo packages.
%global _debugsource_packages 1
%else
# Release RPM only: disable automatic debuginfo/debugsource subpackages.
# The static-bundle release path strips the daemon, which can leave debugsource empty.
%global debug_package %{nil}
%global _debugsource_packages 0
%endif
# Tarball / unpacked dir prefix: %%{srcname}-%%{version} (matches configure.ac AC_INIT / make dist).
# Output RPM/SRPM names use %%{name} = hpcperfstatsd.
Summary: Job-level Monitoring Client
Name: hpcperfstatsd
Version: 3.0
Release: 6%{?dist}
License: GPL
Vendor: Texas Advanced Computing Center
Group: System Environment/Base
Packager: TACC - sharrell@tacc.utexas.edu
Source: %{srcname}-%{version}.tar.gz
#
# Local rpmbuild: scripts/prepare_rpmbuild_dirs.sh removes ./rpmbuild, recreates it, builds
# pinned static deps once into ./embedded-static-prefix/, runs make dist with
# HPC_BUNDLE_EMBED_PREFIX=1 so the tarball includes that tree, and copies the tarball to
# SOURCES. %%build only links hpcperfstatsd against embedded-static-prefix (SKIP_DEPS=1).
# The script prints the rpmbuild -ba command to run next.

# Static third-party archives (libev, rabbitmq-c, LIKWID on x86) ship inside the source
# tarball from prepare; %%build does not recompile them. build_static_bundle.sh still runs
# host probes and configures with --enable-all-static for the daemon link only.

BuildRequires: gcc
BuildRequires: make
BuildRequires: pkgconfig
BuildRequires: systemd-rpm-macros
BuildRequires: gzip
BuildRequires: tar
# configure auto-detects NVIDIA/AMD GPUs via lspci during %%build.
BuildRequires: pciutils
# InfiniBand (libibmad + headers): omit on hosts where IB support is unwanted.
# Omni-Path STL MAD (--enable-opa): optional; requires Cornelis/Intel IFS liboib_utils.
# host_opa sysfs collection is always built (hfi1_*); MAD is additive when IFS is present.
# Intel PVC / XPU Manager (--enable-intel-gpu): vendored third_party/intel-xpum headers; runtime dlopen libxpum.
# BuildRequires: libibmad-devel
# BuildRequires: (site-specific) cornelis-opa / opa-liboib_utils devel for --enable-opa
# RuntimeRequires (PVC nodes): xpumanager providing libxpum.so — not a BuildRequires (dlopen).
BuildRequires: rdma-core-devel

# Non-x86: configure auto-selects DCGM CPU backend (libdcgm). EL9 + NVIDIA repos ship
# DCGM 4 as datacenter-gpu-manager-4-devel (there is no bare "datacenter-gpu-manager" RPM).
# Older stacks may only offer libdcgm-devel.
%ifarch aarch64
BuildRequires: (datacenter-gpu-manager-4-devel or libdcgm-devel)
%endif

%{?systemd_requires}

%description
This package provides the hpcperfstatsd daemon, along with a systemd unit for
control. The daemon publishes job-level host statistics (CPU, memory, optional
InfiniBand, Omni-Path/Cornelis HFI via host_opa, Lustre, NVIDIA DCGM GPU, AMD
GPU, etc.).

The binary is linked with pre-built static libev, rabbitmq-c, and (on x86)
LIKWID archives that are included in the source tarball from
prepare_rpmbuild_dirs.sh, so the RPM does not depend on those distro packages at
runtime. Shared-only stacks (DCGM, optional IB, etc.) remain dynamic per
configure flags.

Optional features and branch highlights: LIKWID (x86) or DCGM (aarch64) CPU
counters, shared DCGM attach (embedded or loopback nv-hostengine), bundled
third_party/nvidia-dcgm API headers, stats buffer/file handling, and RabbitMQ
robustness.

%prep
%setup -q -n %{srcname}-%{version}

%build
cd "%{_builddir}/%{srcname}-%{version}"
export PREFIX="%{_builddir}/%{srcname}-%{version}/embedded-static-prefix"
if test ! -d "${PREFIX}/include" || ! { test -d "${PREFIX}/lib" || test -d "${PREFIX}/lib64"; }; then
  echo "ERROR: embedded-static-prefix/ missing from source tarball; rebuild with scripts/prepare_rpmbuild_dirs.sh" >&2
  exit 1
fi
export SKIP_DEPS=1
export JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
%if 0%{?hpc_debug_build}
export HPC_BUNDLE_RELEASE_BUILD=0
export HPC_BUNDLE_ENABLE_DEBUG=1
export CFLAGS="%{optflags} -g3 -ggdb3 -fno-omit-frame-pointer -fno-inline"
export CXXFLAGS="${CFLAGS}"
export LDFLAGS="${LDFLAGS:-} -Wl,--build-id=sha1"
%else
export HPC_BUNDLE_RELEASE_BUILD=1
%endif
%ifarch aarch64
# Grace Hopper packaging: fail the build if DCGM probing would silently disable nvidia_gpu.
export HPCS_BUNDLE_REQUIRE_DCGM_GPU=1
%endif
# Third-party .a archives come from the tarball; only hpcperfstatsd is compiled here.
./scripts/build_static_bundle.sh
sed -i 's/CONFIGFILE/\%{_sysconfdir}\/hpcperfstats\/hpcperfstats.conf/' src/hpcperfstats.service
%if 0%{?hpc_debug_build}
# Debug /dev/shm verify: fast tier every 30s, full tier every 60s (see rpm_debug_shm_verify.sh FULL).
sed -i 's/^sample_freq .*/sample_freq 30/' src/hpcperfstats.conf
sed -i 's/^sample_freq_slow .*/sample_freq_slow 60/' src/hpcperfstats.conf
%endif

%install
cd "%{_builddir}/%{srcname}-%{version}"
mkdir -p %{buildroot}%{_sbindir}/
mkdir -p %{buildroot}%{_sysconfdir}/hpcperfstats/
mkdir -p %{buildroot}%{_sysconfdir}/sysconfig/
mkdir -p %{buildroot}%{_unitdir}/
install -m 0755 .build-static/src/hpcperfstatsd %{buildroot}%{_sbindir}/hpcperfstatsd
install -m 0644 src/hpcperfstats.conf %{buildroot}%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
install -m 0644 src/hpcperfstatsd.sysconfig %{buildroot}%{_sysconfdir}/sysconfig/hpcperfstatsd
install -m 0644 src/hpcperfstats.service %{buildroot}%{_unitdir}/hpcperfstats.service
%if 0%{?hpc_debug_build}
# Survive EL10 rpmbuild rmbuild: stash capabilities outside BUILD for rpm_debug_shm_verify.sh.
mkdir -p "%{_topdir}/debug-verify"
if test ! -f .build-static/monitor-build-capabilities.json; then
  echo "ERROR: missing .build-static/monitor-build-capabilities.json (debug build must emit capabilities)" >&2
  exit 1
fi
cp -f .build-static/monitor-build-capabilities.json \
  "%{_topdir}/debug-verify/monitor-build-capabilities.json"
%endif

%files
%{_sbindir}/hpcperfstatsd
%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
%config(noreplace) %{_sysconfdir}/sysconfig/hpcperfstatsd
%{_unitdir}/hpcperfstats.service
%dir %{_sysconfdir}/hpcperfstats

%post
# Pick up unit changes (new installs and upgrades) before preset/enable/start.
/usr/bin/systemctl daemon-reload >/dev/null 2>&1 || :
%systemd_post hpcperfstats.service
/usr/bin/systemctl enable hpcperfstats.service >/dev/null 2>&1 || :
if /usr/bin/systemctl --quiet is-active hpcperfstats.service; then
    /usr/bin/systemctl stop hpcperfstats.service >/dev/null 2>&1 || :
fi
/usr/bin/systemctl start hpcperfstats.service >/dev/null 2>&1 || :

%preun
%systemd_preun hpcperfstats.service

%postun
%systemd_postun_with_restart hpcperfstats.service

%changelog
* Tue Jul 21 2026 sharrell@tacc.utexas.edu - 3.0-6
- Debug %%install: stash monitor-build-capabilities.json under %%{_topdir}/debug-verify
  so rpm_debug_shm_verify.sh works after EL10 rpmbuild rmbuild deletes BUILD/.

* Sun Apr 05 2026 sharrell@tacc.utexas.edu - 3.0-5
- Ship embedded-static-prefix inside the source tarball from prepare_rpmbuild_dirs.sh;
  %%build uses SKIP_DEPS=1 and only compiles hpcperfstatsd. Drop %%build-only dep tools
  (curl, cmake, autotools chain for vendored sources, perl/gawk for LIKWID build).

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-4
- Rename package to hpcperfstatsd; source tarball prefix stays hpcperfstats (AC_INIT / make dist).

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-3
- aarch64 BuildRequires: use datacenter-gpu-manager-4-devel or libdcgm-devel (rich dep);
  EL9 NVIDIA DCGM 4 has no package named datacenter-gpu-manager.

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-2
- build_static_bundle.sh: probe IB / DCGM / AMD SDK and use cpu-counter-backend=auto;
  drop %%hpc_extra_configure; add BuildRequires: pciutils, rdma-core-devel.
- Fix ib_ext.c / ib_sw.c InfiniBand includes for C (remove invalid extern-C blocks).

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-1
- Bump upstream version to 3.0 (sync with monitor/configure.ac AC_INIT).
- See monitor/cursor-rules/monitor-version-and-packaging.mdc for version/spec maintenance.

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 2.4-3
- Build via scripts/build_static_bundle.sh (--enable-all-static): pinned static
  libev, rabbitmq-c, LIKWID (x86); drop runtime deps on those distro packages.
- BuildRequires: gcc-c++, cmake, curl, gzip, tar, perl, gawk; aarch64 BR
  libdcgm-devel for DCGM CPU backend link.
- Install binary from .build-static/src/hpcperfstatsd; document offline SKIP_DEPS workflows.

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 2.4-2
- Align packaging notes and BuildRequires with monitor_woooosah branch.
- Document CPU backends (LIKWID / DCGM), DCGM attach (embedded + 127.0.0.1
  nv-hostengine), bundled third_party/nvidia-dcgm headers, and static bundle
  script for dev builds.
- Add build deps: autotools, libev, librabbitmq, likwid, rdma-core (InfiniBand).
- Summarize branch: DCGM CPU counter parity (field watch, /proc/stat + monotonic
  fallbacks, cpufreq sysfs clock fallback), shared dcgm_session for GPU, IB
  detection/collection updates, stats buffer/file cadence and format changes,
  RabbitMQ reconnect handling, AMD GPU + arch-agnostic paths, monitor
  refactor (daemon/cli split), expanded unit tests.
