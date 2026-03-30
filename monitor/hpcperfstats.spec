Summary: Job-level Monitoring Client
Name: hpcperfstats
Version: 3.0
Release: 3%{?dist}
License: GPL
Vendor: Texas Advanced Computing Center
Group: System Environment/Base
Packager: TACC - sharrell@tacc.utexas.edu
Source: hpcperfstats-%{version}.tar.gz
#
# Local rpmbuild: scripts/prepare_rpmbuild_dirs.sh creates ./rpmbuild/*, copies this spec to
# rpmbuild/SPECS/, builds pinned static deps into rpmbuild/static-prefix (same role as
# %%build PREFIX), runs configure+make dist with CPPFLAGS/LDFLAGS/PKG_CONFIG_PATH, and
# copies the tarball to SOURCES. The script prints the rpmbuild -ba command to run next.

# Static bundle: monitor/scripts/build_static_bundle.sh builds pinned libev,
# rabbitmq-c, and (on x86_64/i686) LIKWID as static archives, then configures
# with --enable-all-static. It probes InfiniBand, NVIDIA DCGM, and AMD GPUPerfAPI
# on the build host and passes --disable-* when devel stacks are missing; CPU
# backend is --with-cpu-counter-backend=auto (LIKWID on x86, DCGM elsewhere).
# Runtime does not require system libev, librabbitmq, or likwid packages.

# Toolchain and tools to compile vendored deps (libev: autotools; rabbitmq-c: cmake).
BuildRequires: gcc
BuildRequires: gcc-c++
BuildRequires: make
BuildRequires: autoconf
BuildRequires: automake
BuildRequires: libtool
BuildRequires: cmake >= 3.5
BuildRequires: pkgconfig
BuildRequires: systemd-rpm-macros
BuildRequires: gzip
BuildRequires: tar
# Script downloads pinned source tarballs (override pins via env in %%build if needed).
BuildRequires: curl
# LIKWID static build (x86_64) uses the upstream Makefile; perl/gawk are commonly required.
BuildRequires: perl
BuildRequires: gawk
# configure auto-detects NVIDIA/AMD GPUs via lspci during %%build.
BuildRequires: pciutils
# InfiniBand (libibmad + headers): omit on hosts where IB support is unwanted.
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
InfiniBand, Lustre, NVIDIA DCGM GPU, AMD GPU, etc.).

The binary is built with the monitor's static bundle: libev, rabbitmq-c, and
(on x86) LIKWID are linked statically into hpcperfstatsd so the RPM does not
depend on those libraries at runtime. Shared-only stacks (DCGM, optional IB,
etc.) remain dynamic per configure flags.

Optional features and branch highlights: LIKWID (x86) or DCGM (aarch64) CPU
counters, shared DCGM attach (embedded or loopback nv-hostengine), bundled
third_party/nvidia-dcgm API headers, stats buffer/file handling, and RabbitMQ
robustness.

%prep
%setup -q

%build
cd "%{_builddir}/%{name}-%{version}"
export PREFIX="%{_builddir}/hpcperfstats-static-prefix"
export SRCDIR="%{_builddir}/hpcperfstats-static-src"
export JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
mkdir -p "${PREFIX}/include" "${PREFIX}/lib" "${PREFIX}/lib64" "${PREFIX}/lib/pkgconfig"
# Network access is required on first build to fetch pinned dependency tarballs
# unless you pre-populate ${SRCDIR} / ${PREFIX} and set SKIP_DEPS=1 below.
./scripts/build_static_bundle.sh
sed -i 's/CONFIGFILE/\%{_sysconfdir}\/hpcperfstats\/hpcperfstats.conf/' src/hpcperfstats.service
sed -i 's/localhost/stats.frontera.tacc.utexas.edu/' src/hpcperfstats.conf
sed -i 's/default/frontera/' src/hpcperfstats.conf

%install
cd "%{_builddir}/%{name}-%{version}"
mkdir -p %{buildroot}%{_sbindir}/
mkdir -p %{buildroot}%{_sysconfdir}/hpcperfstats/
mkdir -p %{buildroot}%{_unitdir}/
install -m 0755 .build-static/src/hpcperfstatsd %{buildroot}%{_sbindir}/hpcperfstatsd
install -m 0644 src/hpcperfstats.conf %{buildroot}%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
install -m 0644 src/hpcperfstats.service %{buildroot}%{_unitdir}/hpcperfstats.service

%files
%{_sbindir}/hpcperfstatsd
%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
%{_unitdir}/hpcperfstats.service
%dir %{_sysconfdir}/hpcperfstats

%post
%systemd_post hpcperfstats.service

%preun
%systemd_preun hpcperfstats.service

%postun
%systemd_postun_with_restart hpcperfstats.service

%changelog
* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-3
- aarch64 BuildRequires: use datacenter-gpu-manager-4-devel or libdcgm-devel (rich dep);
  EL9 NVIDIA DCGM 4 has no package named datacenter-gpu-manager.

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-2
- build_static_bundle.sh: probe IB / DCGM / AMD SDK and use cpu-counter-backend=auto;
  drop %%hpc_extra_configure; add BuildRequires: pciutils, rdma-core-devel.
- Fix ib_ext.c / ib_sw.c InfiniBand includes for C (remove invalid extern-C blocks).

* Sat Mar 28 2026 sharrell@tacc.utexas.edu - 3.0-1
- Bump upstream version to 3.0 (sync with monitor/configure.ac AC_INIT).
- See .cursor/rules/monitor-version-and-packaging.mdc for version/spec maintenance.

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
