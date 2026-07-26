AC_INIT([hpcperfstats], [3.0], [sharrell@tacc.utexas.edu])
AM_INIT_AUTOMAKE([foreign -Wall -Werror -Wno-portability])
AC_CANONICAL_HOST
AC_PROG_CC
AM_PROG_CC_C_O

AC_ARG_ENABLE([all-static],
[AS_HELP_STRING([--enable-all-static], [Link most third-party libraries as static archives (linker groups) (default: yes). Use --disable-all-static for dynamic linking where available. Requires static .a for ev, rabbitmq-c, and likwid (+ likwid-hwloc, likwid-lua for the static liblikwid link); for workspace .build/prefix run monitor/scripts/ensure_dotbuild_prefix_static.sh. Vendor stacks usually shipped as shared only (DCGM, RDMA/OPA, libibmad) are linked after -Wl,-Bdynamic. See monitor/scripts/build_static_bundle.sh.])],
[enable_all_static=$enableval],
[enable_all_static=yes])

MONITOR_SHARED_LIBS=""

dnl With --enable-all-static, route vendor / RDMA-style stacks to MONITOR_SHARED_LIBS
dnl so they are linked after -Wl,-Bdynamic (shared .so) while core deps stay static.
AC_DEFUN([MONITOR_CFG_INFINIBAND_LIBS], [
  if test "x$enable_all_static" = xyes; then
    save_LIBS=$LIBS
    LIBS=
    AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad], [have_ib=yes],
      [LIBS=$save_LIBS
       AC_MSG_ERROR([Unable to find libibmad!])])
    if test "x${have_ib}" = xyes; then
      MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
      LIBS=$save_LIBS
      AC_CHECK_HEADERS([infiniband/umad.h infiniband/mad.h], [infiniband=true],
        [AC_MSG_ERROR([Unable to find umad.h or mad.h on this system!])])
    fi
  else
    AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad], [have_ib=yes],
      [AC_MSG_ERROR([Unable to find libibmad!])])
    if test "x${have_ib}" = xyes; then
      AC_CHECK_HEADERS([infiniband/umad.h infiniband/mad.h], [infiniband=true],
        [AC_MSG_ERROR([Unable to find umad.h or mad.h on this system!])])
    fi
  fi
])

AC_DEFUN([MONITOR_CFG_OPA_LIBS], [
  if test "x$enable_all_static" = xyes; then
    save_LIBS=$LIBS
    LIBS=
    AC_SEARCH_LIBS([iba_fstatus_msg], [public])
    AC_SEARCH_LIBS([oib_pa_client_init], [oib_utils], [have_opa=yes],
      [LIBS=$save_LIBS
       AC_MSG_ERROR([Unable to find liboib_utils!])],
      [ -libmad -libumad -libverbs -lpublic -lpthread ])
    if test "x${have_opa}" = xyes; then
      AC_SEARCH_LIBS([ibv_create_cq], [ibverbs])
      AC_SEARCH_LIBS([umad_set_addr], [ibumad])
      AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad])
      MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
      LIBS=$save_LIBS
      AC_CHECK_HEADERS([oib_utils.h], [opa=true],
        [AC_MSG_ERROR([Unable to find oib_utils.h on this system!])])
    fi
  else
    AC_SEARCH_LIBS([iba_fstatus_msg], [public])
    AC_SEARCH_LIBS([oib_pa_client_init], [oib_utils], [have_opa=yes],
      [AC_MSG_ERROR([Unable to find liboib_utils!])],
      [ -libmad -libumad -libverbs -lpublic -lpthread ])
    if test "x${have_opa}" = xyes; then
      AC_CHECK_HEADERS([oib_utils.h], [opa=true],
        [AC_MSG_ERROR([Unable to find oib_utils.h on this system!])])
    fi
    AC_SEARCH_LIBS([ibv_create_cq], [ibverbs])
    AC_SEARCH_LIBS([umad_set_addr], [ibumad])
    AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad])
  fi
])

dnl Static librabbitmq.a needs -lrt (pkg-config Libs.private) on some hosts; try that first.
dnl Unset Autoconf cache before the plain retry (same ac_cv_search_* key).
have_rabbitmq=no
AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
  [], [-lrt -pthread])
if test "x${have_rabbitmq}" != xyes; then
  AS_UNSET([ac_cv_search_amqp_new_connection])
  AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
    [AC_MSG_ERROR([Unable to find librabbitmq!])])
fi
if test "x${have_rabbitmq}" = xyes; then
   AC_CHECK_HEADERS([rabbitmq-c/amqp.h], [rabbitmq=true],
   [AC_MSG_ERROR([Unable to find rabbitmq-c/amqp.h (rabbitmq-c development headers)!])])
fi
AM_CONDITIONAL([RABBITMQ], [test x$rabbitmq = xtrue])

AC_ARG_ENABLE([hardware],
[  --enable-hardware	Enable processor hardware counter support (default is true)],
[case "${enableval}" in
  yes) hardware=true ;;
  no)  hardware=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-hardware]) ;;
esac],
[hardware=true])
AM_CONDITIONAL([HARDWARE], [test x$hardware = xtrue])

AC_ARG_WITH([cpu-counter-backend],
[AS_HELP_STRING([--with-cpu-counter-backend={auto|likwid|dcgm}], [CPU counter backend selection (default: auto)])],
[cpu_counter_backend="$withval"],
[cpu_counter_backend="auto"])

case "$cpu_counter_backend" in
  auto|likwid|dcgm) ;;
  *) AC_MSG_ERROR([bad value ${cpu_counter_backend} for --with-cpu-counter-backend]) ;;
esac

is_x86=false
case "$host_cpu" in
  x86_64|i?86) is_x86=true ;;
esac

likwid=false
dcgm_checked=false
cpu_papi_flops=false
CPU_COUNTER_BACKEND_CPPFLAG=""
MONITOR_ARCH_CPPFLAG=""

if test "x$hardware" != "xtrue"; then
  cpu_counter_backend="none"
  AC_MSG_NOTICE([CPU counter backend: disabled (--disable-hardware)])
else
if test "x$cpu_counter_backend" = "xauto"; then
  if test "x$is_x86" = "xtrue"; then
    cpu_counter_backend="likwid"
  else
    cpu_counter_backend="dcgm"
  fi
fi

if test "x$cpu_counter_backend" = "xlikwid" && test "x$is_x86" != "xtrue"; then
  AC_MSG_ERROR([LIKWID CPU backend is only supported on x86 (host_cpu=${host_cpu}); use --with-cpu-counter-backend=dcgm on non-x86])
fi

if test "x$cpu_counter_backend" = "xlikwid"; then
  dnl Static liblikwid.a (prefix-static, foreign cross deps) needs hwloc/lua archives and -lm on the
  dnl probe link line. AC_SEARCH_LIBS only adds -llikwid to LIBS; extras are for the try link only.
  dnl Try the static-bundle link first, then fall back to a shared-only -llikwid (distro packages).
  have_likwid=no
  likwid_static_bundle=no
  AC_SEARCH_LIBS([perfmon_init], [likwid], [have_likwid=yes likwid_static_bundle=yes],
    [], [-llikwid-hwloc -llikwid-lua -lm])
  if test "x${have_likwid}" != xyes; then
    AC_SEARCH_LIBS([perfmon_init], [likwid], [have_likwid=yes],
      [AC_MSG_ERROR([Unable to find liblikwid!])])
  fi
  if test "x${have_likwid}" = xyes; then
    AC_CHECK_HEADERS([likwid.h], [likwid=true],
      [AC_MSG_ERROR([Unable to find likwid.h!])])
  fi
  dnl Static LIKWID: nested liblikwid-hwloc.a provides likwid_hwloc_*; liblikwid-lua.a for Lua.
  dnl Do not append -lm here: final link uses shared libm outside -Wl,-Bstatic; probe above used -lm only for the try link.
  if test "x$enable_all_static" = xyes || test "x$likwid_static_bundle" = xyes; then
    case " ${LIBS} " in
      *" -llikwid-hwloc "*) ;;
      *) LIBS="$LIBS -llikwid-hwloc -llikwid-lua" ;;
    esac
  fi
fi

if test "x$cpu_counter_backend" = "xdcgm"; then
  if test "x$enable_all_static" = xyes; then
    save_LIBS=$LIBS
    LIBS=
    AC_SEARCH_LIBS([dcgmInit], [dcgm],
      [MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
       have_dcgm=yes
       LIBS=$save_LIBS],
      [LIBS=$save_LIBS
       AC_MSG_ERROR([Unable to find libdcgm (required for DCGM CPU backend)])])
  else
    AC_SEARCH_LIBS([dcgmInit], [dcgm], [have_dcgm=yes],
      [AC_MSG_ERROR([Unable to find libdcgm (required for DCGM CPU backend)])])
  fi
  if test "x${have_dcgm}" = xyes; then
    save_CPPFLAGS=$CPPFLAGS
    CPPFLAGS="-I${srcdir}/third_party/nvidia-dcgm ${CPPFLAGS}"
    AC_CHECK_HEADERS([dcgm_agent.h dcgm_structs.h], [dcgm_headers=true],
      [AC_MSG_ERROR([Unable to find dcgm_agent.h/dcgm_structs.h for DCGM CPU backend (see third_party/nvidia-dcgm/README)])])
    CPPFLAGS=$save_CPPFLAGS
  fi
  dcgm_checked=true
  CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_DCGM"
  MONITOR_ARCH_CPPFLAG=""
  dnl Grace / aarch64 hybrid: DCGM util+power + static PAPI for FLOPs/cycles.
  cpu_papi_flops=false
  if test "x$is_x86" != "xtrue"; then
    AC_CHECK_HEADERS([papi.h], [],
      [AC_MSG_ERROR([papi.h required for non-x86 DCGM CPU backend (PAPI FLOPs/cycles hybrid)])])
    AC_SEARCH_LIBS([PAPI_library_init], [papi], [have_papi=yes],
      [AC_MSG_ERROR([Unable to find libpapi (required for non-x86 DCGM+PAPI hybrid CPU path)])],
      [-lpfm -lm])
    case " ${LIBS} " in
      *" -lpfm "*) ;;
      *)
        AC_CHECK_LIB([pfm], [pfm_initialize], [LIBS="$LIBS -lpfm"], [])
        ;;
    esac
    CPU_COUNTER_BACKEND_CPPFLAG="${CPU_COUNTER_BACKEND_CPPFLAG} -DMONITOR_CPU_PAPI_FLOPS"
    cpu_papi_flops=true
    AC_MSG_NOTICE([CPU counter hybrid: DCGM util/power + PAPI FLOPs/cycles])
  fi
elif test "x$cpu_counter_backend" = "xlikwid"; then
  CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_LIKWID"
fi

if test "x$hardware" = "xtrue"; then
  AC_MSG_NOTICE([CPU counter backend: ${cpu_counter_backend} (host_cpu=${host_cpu})])
fi
fi
AC_SUBST([CPU_COUNTER_BACKEND_CPPFLAG])

AC_ARG_WITH([monitor-arch],
[AS_HELP_STRING([--with-monitor-arch={intel|amd|auto}], [Compile-time LIKWID architecture selection (default: auto)])],
[monitor_arch="$withval"],
[monitor_arch="auto"])

case "$monitor_arch" in
  intel|amd|auto) ;;
  *) AC_MSG_ERROR([bad value ${monitor_arch} for --with-monitor-arch]) ;;
esac

if test "x$monitor_arch" = "xauto"; then
  case "$host_cpu" in
    x86_64|i?86)
      monitor_arch="intel"
      # Prefer AuthenticAMD build hosts so MONITOR_ARCH_AMD matches the machine.
      # RAPL MSR selection is runtime (likwid_rapl_collect_path); this flag must not
      # force Intel power_read on EPYC.
      if test -r /proc/cpuinfo && \
         grep -m1 -E '^vendor_id[[:space:]]*:[[:space:]]*AuthenticAMD$' /proc/cpuinfo >/dev/null 2>&1; then
        monitor_arch="amd"
      fi
      ;;
    aarch64|arm*|ppc64*|riscv64) monitor_arch="intel" ;;
    *) monitor_arch="intel" ;;
  esac
fi
AC_MSG_NOTICE([--with-monitor-arch resolved to: ${monitor_arch}])

if test "x$cpu_counter_backend" = "xlikwid"; then
  if test "x$monitor_arch" = "xintel"; then
    MONITOR_ARCH_CPPFLAG="-DMONITOR_ARCH_INTEL"
  else
    MONITOR_ARCH_CPPFLAG="-DMONITOR_ARCH_AMD"
  fi
fi
AC_SUBST([MONITOR_ARCH_CPPFLAG])
AM_CONDITIONAL([MONITOR_ARCH_INTEL], [test "x$monitor_arch" = "xintel"])
AM_CONDITIONAL([MONITOR_ARCH_AMD], [test "x$monitor_arch" = "xamd"])
monitor_arch_arm=false
case "$host_cpu" in
  aarch64|arm*) monitor_arch_arm=true ;;
esac
AM_CONDITIONAL([MONITOR_ARCH_ARM], [test x$monitor_arch_arm = xtrue])

AM_CONDITIONAL([LIKWID], [test x$likwid = xtrue])
AM_CONDITIONAL([CPU_BACKEND_LIKWID],
  [test x$hardware = xtrue -a x$cpu_counter_backend = xlikwid])
AM_CONDITIONAL([CPU_BACKEND_DCGM],
  [test x$hardware = xtrue -a x$cpu_counter_backend = xdcgm])
AM_CONDITIONAL([CPU_PAPI_FLOPS],
  [test x$hardware = xtrue -a x$cpu_counter_backend = xdcgm -a x$cpu_papi_flops = xtrue])

AC_ARG_ENABLE([infiniband],
[  --enable-infiniband	Enable InfiniBand MAD support (default is true; use --enable-ib-mad-dlopen for fleet)],
[case "${enableval}" in
  yes) : ;;
  no)  infiniband=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-infiniband]) ;;
esac],
[infiniband=])

AC_ARG_ENABLE([ib-mad-dlopen],
[AS_HELP_STRING([--enable-ib-mad-dlopen],
  [Compile host_ib MAD against vendored mad.h and dlopen libibmad at runtime (no -libmad; Stampede3 fleet)])],
[case "${enableval}" in
  yes) ib_mad_dlopen=true ;;
  no)  ib_mad_dlopen=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-ib-mad-dlopen]) ;;
esac],
[ib_mad_dlopen=false])

if test "x$ib_mad_dlopen" = xtrue; then
  infiniband=true
  AC_MSG_NOTICE([InfiniBand MAD: runtime libibmad dlopen (vendored third_party/ibmad-shim)])
elif test "x$infiniband" = xfalse; then
  :
elif test "x$infiniband" = x; then
  MONITOR_CFG_INFINIBAND_LIBS
elif test "x$infiniband" != xfalse; then
  MONITOR_CFG_INFINIBAND_LIBS
fi
AM_CONDITIONAL([INFINIBAND], [test x$infiniband = xtrue])
AM_CONDITIONAL([IB_MAD_DLOPEN], [test x$infiniband = xtrue -a x$ib_mad_dlopen = xtrue])

AC_ARG_ENABLE([lustre],
[  --enable-lustre	Enable Lustre support (default is true)],
[case "${enableval}" in
  yes) lustre=true ;;
  no)  lustre=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-lustre]) ;;
esac],
[lustre=true])
AM_CONDITIONAL([LUSTRE], [test x$lustre = xtrue])

AC_ARG_ENABLE([beegfs],
[  --enable-beegfs	Enable BeeGFS client support (default is true)],
[case "${enableval}" in
  yes) beegfs=true ;;
  no)  beegfs=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-beegfs]) ;;
esac],
[beegfs=true])
AM_CONDITIONAL([BEEGFS], [test x$beegfs = xtrue])

gpu_user_requested=no
AC_ARG_ENABLE([gpu],
[  --enable-gpu		Enable Nvidia GPU support (default is auto-detect)],
[case "${enableval}" in
  yes) gpu=true; gpu_user_requested=yes ;;
  no)  gpu=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-gpu]) ;;
esac],
[gpu=auto])

gpu_dlopen=false
if test "x$is_x86" = "xtrue" && test "x$cpu_counter_backend" = "xlikwid"; then
  gpu_dlopen=true
fi

gpu_autodetect_result="not-run"
if test "x$gpu" = "xauto"; then
  if test "x$gpu_dlopen" = "xtrue"; then
    gpu=true
    gpu_autodetect_result="x86-runtime-dlopen"
  else
    gpu=false
    gpu_autodetect_result="no-nvidia"
    if test -f "${srcdir}/scripts/gpu_lspci_probe.sh"; then
      if sh "${srcdir}/scripts/gpu_lspci_probe.sh" nvidia; then
        gpu=true
        gpu_autodetect_result="nvidia-found"
      fi
    else
      AC_PATH_PROG([LSPCI], [lspci], [no])
      if test "x$LSPCI" != "xno"; then
        if $LSPCI -nn 2>/dev/null | awk -v vendor=nvidia -f "${srcdir}/scripts/gpu_lspci_detect.awk"; then
          gpu=true
          gpu_autodetect_result="nvidia-found"
        fi
      else
        gpu_autodetect_result="lspci-missing"
      fi
    fi
  fi
fi

if test "x$gpu" = "xtrue"; then
  if test "x$dcgm_checked" = "xfalse"; then
    if test "x$gpu_dlopen" = "xtrue"; then
      save_CPPFLAGS=$CPPFLAGS
      CPPFLAGS="-I${srcdir}/third_party/nvidia-dcgm ${CPPFLAGS}"
      have_gpu=no
      AC_CHECK_HEADERS([dcgm_agent.h dcgm_structs.h], [have_gpu=yes],
        [if test "x$gpu_user_requested" = "xyes"; then
           AC_MSG_ERROR([Unable to find dcgm_agent.h/dcgm_structs.h (see third_party/nvidia-dcgm/README)])
         else
           gpu=false
           gpu_autodetect_result="${gpu_autodetect_result}-no-hdr"
           AC_MSG_NOTICE([NVIDIA GPU enabled but DCGM headers are missing; disabling gpu])
         fi])
      CPPFLAGS=$save_CPPFLAGS
      if test "x$gpu" = "xtrue"; then
        gpu_autodetect_result="${gpu_autodetect_result}-vendored-hdr"
      fi
    else
    if test "x$enable_all_static" = xyes; then
      save_LIBS=$LIBS
      LIBS=
      AC_SEARCH_LIBS([dcgmInit], [dcgm],
        [MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
         have_gpu=yes
         LIBS=$save_LIBS],
        [LIBS=$save_LIBS
         if test "x$gpu_user_requested" = "xyes"; then
           AC_MSG_ERROR([Unable to find libdcgm!])
         else
           gpu=false
           gpu_autodetect_result="${gpu_autodetect_result}-no-sdk"
           AC_MSG_NOTICE([NVIDIA GPU hardware detected but libdcgm is missing; disabling gpu])
         fi])
    else
      AC_SEARCH_LIBS([dcgmInit], [dcgm], [have_gpu=yes],
        [if test "x$gpu_user_requested" = "xyes"; then
           AC_MSG_ERROR([Unable to find libdcgm!])
         else
           gpu=false
           gpu_autodetect_result="${gpu_autodetect_result}-no-sdk"
           AC_MSG_NOTICE([NVIDIA GPU hardware detected but libdcgm is missing; disabling gpu])
         fi])
    fi
    if test "x${have_gpu}" = xyes; then
      save_CPPFLAGS=$CPPFLAGS
      CPPFLAGS="-I${srcdir}/third_party/nvidia-dcgm ${CPPFLAGS}"
      AC_CHECK_HEADERS([dcgm_agent.h dcgm_structs.h], [gpu=true],
        [if test "x$gpu_user_requested" = "xyes"; then
           AC_MSG_ERROR([Unable to find dcgm_agent.h/dcgm_structs.h (see third_party/nvidia-dcgm/README)])
         else
           gpu=false
           gpu_autodetect_result="${gpu_autodetect_result}-no-sdk"
           AC_MSG_NOTICE([NVIDIA GPU enabled but DCGM headers are missing; disabling gpu])
         fi])
      CPPFLAGS=$save_CPPFLAGS
    fi
    fi
  else
    gpu=true
  fi
fi

if test "x$gpu" = "xtrue"; then
  if test "x$gpu_dlopen" = "xtrue"; then
    AC_MSG_NOTICE([GPU summary: enabled (runtime dcgm dlopen on x86 LIKWID); detect=${gpu_autodetect_result}])
  else
    AC_MSG_NOTICE([GPU summary: enabled (DCGM link-time); detect=${gpu_autodetect_result}])
  fi
else
  AC_MSG_NOTICE([GPU summary: disabled; detect=${gpu_autodetect_result}])
fi
AM_CONDITIONAL([GPU], [test x$gpu = xtrue])
AM_CONDITIONAL([GPU_DLOPEN], [test x$gpu = xtrue -a x$gpu_dlopen = xtrue])
AM_CONDITIONAL([MONITOR_USE_DCGM_VENDOR_INCLUDES],
  [test "x$cpu_counter_backend" = "xdcgm" -o "x$gpu" = "xtrue"])

amd_gpu_user_requested=no
AC_ARG_ENABLE([amd-gpu],
[  --enable-amd-gpu	Enable AMD GPU support via GPUPerfAPI (default is auto-detect)],
[case "${enableval}" in
  yes) amd_gpu=true; amd_gpu_user_requested=yes ;;
  no)  amd_gpu=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-amd-gpu]) ;;
esac],
[amd_gpu=auto])

amd_gpu_autodetect_result="not-run"
if test "x$amd_gpu" = "xauto"; then
  amd_gpu=false
  amd_gpu_autodetect_result="no-amd"
  AC_PATH_PROG([LSPCI], [lspci], [no])
  if test "x$LSPCI" != "xno"; then
    if $LSPCI -nn 2>/dev/null | awk '{
      l=tolower($0);
      if ((index(l, "vga compatible controller") || index(l, "3d controller")) &&
          (index(l, "advanced micro devices") || index(l, " amd/ati "))) found=1
    } END { exit(found ? 0 : 1) }'; then
      amd_gpu=true
      amd_gpu_autodetect_result="amd-found"
    fi
  else
    amd_gpu_autodetect_result="lspci-missing"
  fi
fi

if test "x$amd_gpu" = "xtrue"; then
  AC_CHECK_HEADERS([gpu_performance_api/gpu_perf_api.h], [amd_gpu=true],
    [if test "x$amd_gpu_user_requested" = "xyes"; then
       AC_MSG_ERROR([Unable to find gpu_performance_api/gpu_perf_api.h (GPUPerfAPI) for AMD GPU support])
     else
       amd_gpu=false
       amd_gpu_autodetect_result="${amd_gpu_autodetect_result}-no-sdk"
       AC_MSG_NOTICE([AMD GPU hardware detected but GPUPerfAPI headers are missing; disabling amd_gpu])
     fi])
fi

if test "x$amd_gpu" = "xtrue"; then
  AC_MSG_NOTICE([AMD GPU summary: enabled (GPUPerfAPI path); detect=${amd_gpu_autodetect_result}])
else
  AC_MSG_NOTICE([AMD GPU summary: disabled; detect=${amd_gpu_autodetect_result}])
fi
AM_CONDITIONAL([AMD_GPU], [test x$amd_gpu = xtrue])

intel_gpu_user_requested=no
AC_ARG_ENABLE([intel-gpu],
[  --enable-intel-gpu	Enable Intel Data Center GPU (PVC) via XPU Manager dlopen (default is auto-detect)],
[case "${enableval}" in
  yes) intel_gpu=true; intel_gpu_user_requested=yes ;;
  no)  intel_gpu=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-intel-gpu]) ;;
esac],
[intel_gpu=auto])

intel_gpu_autodetect_result="not-run"
if test "x$intel_gpu" = "xauto"; then
  intel_gpu=false
  intel_gpu_autodetect_result="no-intel-dc"
  if test -f "${srcdir}/scripts/gpu_lspci_probe.sh"; then
    if sh "${srcdir}/scripts/gpu_lspci_probe.sh" intel; then
      intel_gpu=true
      intel_gpu_autodetect_result="intel-dc-found"
    fi
  else
    AC_PATH_PROG([LSPCI], [lspci], [no])
    if test "x$LSPCI" != "xno"; then
      if $LSPCI -nn 2>/dev/null | awk -v vendor=intel -f "${srcdir}/scripts/gpu_lspci_detect.awk"; then
        intel_gpu=true
        intel_gpu_autodetect_result="intel-dc-found"
      fi
    else
      intel_gpu_autodetect_result="lspci-missing"
    fi
  fi
fi

if test "x$intel_gpu" = "xtrue"; then
  save_CPPFLAGS=$CPPFLAGS
  CPPFLAGS="-I${srcdir}/third_party/intel-xpum ${CPPFLAGS}"
  AC_CHECK_HEADERS([xpum_api.h xpum_structs.h], [intel_gpu=true],
    [if test "x$intel_gpu_user_requested" = "xyes"; then
       AC_MSG_ERROR([Unable to find xpum_api.h/xpum_structs.h (see third_party/intel-xpum/README)])
     else
       intel_gpu=false
       intel_gpu_autodetect_result="${intel_gpu_autodetect_result}-no-hdr"
       AC_MSG_NOTICE([Intel GPU enabled but XPUM headers are missing; disabling intel_gpu])
     fi])
  CPPFLAGS=$save_CPPFLAGS
  if test "x$intel_gpu" = "xtrue"; then
    intel_gpu_autodetect_result="${intel_gpu_autodetect_result}-vendored-hdr"
  fi
fi

if test "x$intel_gpu" = "xtrue"; then
  AC_MSG_NOTICE([Intel GPU summary: enabled (runtime libxpum dlopen); detect=${intel_gpu_autodetect_result}])
else
  AC_MSG_NOTICE([Intel GPU summary: disabled; detect=${intel_gpu_autodetect_result}])
fi
AM_CONDITIONAL([INTEL_GPU], [test x$intel_gpu = xtrue])

AC_ARG_ENABLE([opa],
[  --enable-opa		Enable Omni-Path STL MAD via link-time liboib_utils (default is false)],
[case "${enableval}" in
  yes) opa_link=true ;;
  no)  opa_link=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-opa]) ;;
esac],
[opa_link=false])

AC_ARG_ENABLE([opa-mad-dlopen],
[AS_HELP_STRING([--enable-opa-mad-dlopen],
  [Compile host_opa STL MAD against vendored oib-shim and dlopen liboib_utils at runtime (Stampede3 fleet)])],
[case "${enableval}" in
  yes) opa_mad_dlopen=true ;;
  no)  opa_mad_dlopen=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-opa-mad-dlopen]) ;;
esac],
[opa_mad_dlopen=false])

opa=false
if test "x$opa_mad_dlopen" = xtrue; then
  opa=true
  AC_MSG_NOTICE([Omni-Path STL MAD: runtime liboib_utils dlopen (vendored third_party/oib-shim)])
elif test "x$opa_link" = xtrue; then
  opa=true
  MONITOR_CFG_OPA_LIBS
fi
AM_CONDITIONAL([OPA], [test x$opa = xtrue])
AM_CONDITIONAL([OPA_MAD_DLOPEN], [test x$opa = xtrue -a x$opa_mad_dlopen = xtrue])
AM_CONDITIONAL([OPA_LINK], [test x$opa = xtrue -a x$opa_mad_dlopen != xtrue])

AC_ARG_ENABLE([metric-profiler],
[AS_HELP_STRING([--enable-metric-profiler], [Enable per-collect timing profiler (default: no)])],
[case "${enableval}" in
  yes) metric_profiler=true ;;
  no)  metric_profiler=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-metric-profiler]) ;;
esac],
[metric_profiler=false])

AC_ARG_WITH([metric-profiler-backend],
[AS_HELP_STRING([--with-metric-profiler-backend={none,ebpf}],
  [Metric profiler backend (default: none)])],
[metric_profiler_backend="$withval"],
[metric_profiler_backend="none"])

case "$metric_profiler_backend" in
  none|ebpf) ;;
  *) AC_MSG_ERROR([bad value ${metric_profiler_backend} for --with-metric-profiler-backend]) ;;
esac

METRIC_PROFILER_CPPFLAG=""
METRIC_PROFILER_BACKEND_CPPFLAG=""
if test "x$metric_profiler" = xtrue; then
  METRIC_PROFILER_CPPFLAG="-DMONITOR_METRIC_PROFILER"
  if test "x$metric_profiler_backend" = xebpf; then
    METRIC_PROFILER_BACKEND_CPPFLAG="-DMONITOR_METRIC_PROFILER_EBPF"
  fi
fi
AC_SUBST([METRIC_PROFILER_CPPFLAG])
AC_SUBST([METRIC_PROFILER_BACKEND_CPPFLAG])
AM_CONDITIONAL([METRIC_PROFILER], [test x$metric_profiler = xtrue])
AM_CONDITIONAL([METRIC_PROFILER_BACKEND_EBPF],
  [test x$metric_profiler = xtrue && test x$metric_profiler_backend = xebpf])

AC_ARG_ENABLE([debug],
[  --enable-debug	Turn on debugging (default is false)],
[case "${enableval}" in
  yes) debug=true ;;     
  no)  debug=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-debug]) ;;
esac],
[debug=false])
AM_CONDITIONAL([DEBUG], [test x$debug = xtrue])

dnl Static libev.a (prefix-static, foreign cross) needs -lm on the probe link; try that first.
have_libev=no
AC_SEARCH_LIBS([ev_run], [ev], [have_libev=yes libev=yes],
  [], [-lm])
if test "x${have_libev}" != xyes; then
  AC_SEARCH_LIBS([ev_run], [ev], [libev=yes],
    [AC_MSG_ERROR([Failed to find libev])])
fi
AC_CHECK_HEADERS([ev.h], [ev=true], [AC_MSG_ERROR([Failed to find header ev.h])])

if test "x$enable_all_static" = xyes; then
  if test -n "$MONITOR_SHARED_LIBS"; then
    AC_MSG_NOTICE([--enable-all-static: linking shared-only libs after static group:${MONITOR_SHARED_LIBS}])
  fi
  LIBS="-Wl,-Bstatic -Wl,--start-group $LIBS -Wl,--end-group -Wl,-Bdynamic -lpthread -ldl${MONITOR_SHARED_LIBS}"
fi

PKG_PROG_PKG_CONFIG
AC_ARG_WITH([systemduserunitdir],
     [AS_HELP_STRING([--with-systemduserunitdir=DIR], [Directory for systemd user service files])],,
     [with_systemduserunitdir=auto])
AS_IF([test "x$with_systemduserunitdir" = "xyes" -o "x$with_systemduserunitdir" = "xauto"], [
     def_systemduserunitdir=$($PKG_CONFIG --variable=systemduserunitdir systemd)
     AS_IF([test "x$def_systemduserunitdir" = "x"],
   [AS_IF([test "x$with_systemduserunitdir" = "xyes"],
    [AC_MSG_ERROR([systemd support requested but pkg-config unable to query systemd package])])
    with_systemduserunitdir=no],
   [with_systemduserunitdir="$def_systemduserunitdir"])])
AS_IF([test "x$with_systemduserunitdir" != "xno"],
      [AC_SUBST([systemduserunitdir], [$with_systemduserunitdir])])
AM_CONDITIONAL([HAVE_SYSTEMD], [test "x$with_systemduserunitdir" != "xno"])

MONITOR_FEATURE_CPPFLAGS=""
if test "x$hardware" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_HARDWARE"
fi
if test "x$infiniband" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_INFINIBAND"
  if test "x$ib_mad_dlopen" = "xtrue"; then
    MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_IB_MAD_DLOPEN"
  fi
fi
if test "x$lustre" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_LUSTRE"
fi
if test "x$beegfs" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_BEEGFS"
fi
if test "x$gpu" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_GPU"
  if test "x$gpu_dlopen" = "xtrue"; then
    MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_GPU_DCGM_DLOPEN"
  fi
fi
if test "x$amd_gpu" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_AMD_GPU"
fi
if test "x$intel_gpu" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_INTEL_GPU"
fi
if test "x$opa" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_WITH_OPA"
  if test "x$opa_mad_dlopen" = "xtrue"; then
    MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_OPA_MAD_DLOPEN"
  fi
fi
if test "x$monitor_arch_arm" = "xtrue"; then
  MONITOR_FEATURE_CPPFLAGS="$MONITOR_FEATURE_CPPFLAGS -DMONITOR_HOST_IS_ARM"
fi
AC_SUBST([MONITOR_FEATURE_CPPFLAGS])

AC_CONFIG_FILES([Makefile src/Makefile tests/Makefile])
AC_OUTPUT

