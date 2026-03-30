AC_INIT([hpcperfstats], [3.0], [sharrell@tacc.utexas.edu])
AM_INIT_AUTOMAKE([foreign -Wall -Werror -Wno-portability])
dnl Avoid Makefile rules that re-run autoconf/automake during make dist / normal builds.
AM_MAINTAINER_MODE([disable])
AC_CANONICAL_HOST
AC_PROG_CC
AM_PROG_CC_C_O

AC_ARG_ENABLE([all-static],
[AS_HELP_STRING([--enable-all-static], [Link most third-party libraries as static archives (linker groups) (default: yes). Use --disable-all-static for dynamic linking where available. Requires static .a for ev, rabbitmq-c, and likwid (+ likwid-hwloc, likwid-lua for the static liblikwid link); for workspace .build/prefix run monitor/scripts/ensure_dotbuild_prefix_static.sh. Vendor stacks usually shipped as shared only (DCGM, RDMA/OPA, libibmad, MIC) are linked after -Wl,-Bdynamic. See monitor/scripts/build_static_bundle.sh.])],
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

AC_DEFUN([MONITOR_CFG_MIC_LIBS], [
  if test "x$enable_all_static" = xyes; then
    save_LIBS=$LIBS
    LIBS=
    AC_SEARCH_LIBS([mic_open_device], [micmgmt], [have_mic=yes],
      [LIBS=$save_LIBS
       AC_MSG_ERROR([Unable to find libmicmgmt!])])
    MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
    LIBS=$save_LIBS
  else
    AC_SEARCH_LIBS([mic_open_device], [micmgmt], [have_mic=yes],
      [AC_MSG_ERROR([Unable to find libmicmgmt!])])
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

AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
    [AC_MSG_ERROR([Unable to find librabbitmq!])])
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
  dnl Static liblikwid.a needs hwloc/lua archives and -lm on the same link line; AC_SEARCH_LIBS
  dnl probes with -llikwid only unless we pass other libs for the try link.
  if test "x$enable_all_static" = xyes; then
    AC_SEARCH_LIBS([perfmon_init], [likwid], [have_likwid=yes],
      [AC_MSG_ERROR([Unable to find liblikwid!])],
      [-llikwid-hwloc -llikwid-lua -lm])
  else
    AC_SEARCH_LIBS([perfmon_init], [likwid], [have_likwid=yes],
      [AC_MSG_ERROR([Unable to find liblikwid!])])
  fi
  if test "x${have_likwid}" = xyes; then
    AC_CHECK_HEADERS([likwid.h], [likwid=true],
      [AC_MSG_ERROR([Unable to find likwid.h!])])
  fi
  dnl Static LIKWID: nested liblikwid-hwloc.a provides likwid_hwloc_*; liblikwid-lua.a for Lua.
  dnl Do not append -lm here: final link uses shared libm outside -Wl,-Bstatic; probe above used -lm only for the try link.
  if test "x$enable_all_static" = xyes; then
    LIBS="$LIBS -llikwid-hwloc -llikwid-lua"
  fi
fi
AM_CONDITIONAL([LIKWID], [test x$likwid = xtrue])
AM_CONDITIONAL([CPU_BACKEND_LIKWID], [test x$cpu_counter_backend = xlikwid])
AM_CONDITIONAL([CPU_BACKEND_DCGM], [test x$cpu_counter_backend = xdcgm])

dcgm_checked=false
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
else
  CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_LIKWID"
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
    x86_64|i?86) monitor_arch="intel" ;;
    aarch64|arm*|ppc64*|riscv64) monitor_arch="intel" ;;
    *) monitor_arch="intel" ;;
  esac
fi

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

AC_MSG_NOTICE([CPU counter backend: ${cpu_counter_backend} (host_cpu=${host_cpu})])

AC_ARG_ENABLE([infiniband],
[  --enable-infiniband	Enable Infiniband support (default is true)],
[case "${enableval}" in
  yes) MONITOR_CFG_INFINIBAND_LIBS ;;
  no)  infiniband=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-infiniband]) ;;
esac],
[MONITOR_CFG_INFINIBAND_LIBS
])
AM_CONDITIONAL([INFINIBAND], [test x$infiniband = xtrue])

AC_ARG_ENABLE([lustre],
[  --enable-lustre	Enable Lustre support (default is true)],
[case "${enableval}" in
  yes) lustre=true ;;
  no)  lustre=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-lustre]) ;;
esac],
[lustre=true])
AM_CONDITIONAL([LUSTRE], [test x$lustre = xtrue])

AC_ARG_ENABLE([mic],
[  --enable-mic		Enable Intel MIC (Xeon Phi) support (default is false)],
[case "${enableval}" in
  yes) mic=true
  MONITOR_CFG_MIC_LIBS
  ;;
  no)  mic=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-mic]) ;;
esac],
[mic=false])
AM_CONDITIONAL([MIC], [test x$mic = xtrue])

AC_ARG_ENABLE([gpu],
[  --enable-gpu		Enable Nvidia GPU support (default is auto-detect)],
[case "${enableval}" in
  yes) gpu=true ;;
  no)  gpu=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-gpu]) ;;
esac],
[gpu=auto])

gpu_autodetect_result="not-run"
if test "x$gpu" = "xauto"; then
  gpu=false
  gpu_autodetect_result="no-nvidia"
  AC_PATH_PROG([LSPCI], [lspci], [no])
  if test "x$LSPCI" != "xno"; then
    dnl Use match() not index(): m4 treats index() as a macro and corrupts configure.
    dnl Match common PCI classes for NVIDIA GPUs (3D, Display, Processing accelerators, VGA).
    if $LSPCI -nn 2>/dev/null | awk '{ l=tolower($0); if ((match(l,/vga compatible controller/) || match(l,/3d controller/) || match(l,/display controller/) || match(l,/processing accelerators/)) && match(l,/nvidia/)) found=1 } END { exit(found ? 0 : 1) }'; then
      gpu=true
      gpu_autodetect_result="nvidia-found"
    fi
  else
    gpu_autodetect_result="lspci-missing"
  fi
fi

if test "x$gpu" = "xtrue"; then
  if test "x$dcgm_checked" = "xfalse"; then
    if test "x$enable_all_static" = xyes; then
      save_LIBS=$LIBS
      LIBS=
      AC_SEARCH_LIBS([dcgmInit], [dcgm],
        [MONITOR_SHARED_LIBS="$MONITOR_SHARED_LIBS $LIBS"
         have_gpu=yes
         LIBS=$save_LIBS],
        [LIBS=$save_LIBS
         AC_MSG_ERROR([Unable to find libdcgm!])])
    else
      AC_SEARCH_LIBS([dcgmInit], [dcgm], [have_gpu=yes],
        [AC_MSG_ERROR([Unable to find libdcgm!])])
    fi
    if test "x${have_gpu}" = xyes; then
      save_CPPFLAGS=$CPPFLAGS
      CPPFLAGS="-I${srcdir}/third_party/nvidia-dcgm ${CPPFLAGS}"
      AC_CHECK_HEADERS([dcgm_agent.h dcgm_structs.h], [gpu=true],
        [AC_MSG_ERROR([Unable to find dcgm_agent.h/dcgm_structs.h (see third_party/nvidia-dcgm/README)])])
      CPPFLAGS=$save_CPPFLAGS
    fi
  else
    gpu=true
  fi
fi

if test "x$gpu" = "xtrue"; then
  AC_MSG_NOTICE([GPU summary: enabled (DCGM required); detect=${gpu_autodetect_result}])
else
  AC_MSG_NOTICE([GPU summary: disabled; detect=${gpu_autodetect_result}])
fi
AM_CONDITIONAL([GPU], [test x$gpu = xtrue])
AM_CONDITIONAL([MONITOR_USE_DCGM_VENDOR_INCLUDES],
  [test "x$cpu_counter_backend" = "xdcgm" -o "x$gpu" = "xtrue"])

AC_ARG_ENABLE([amd-gpu],
[  --enable-amd-gpu	Enable AMD GPU support via GPUPerfAPI (default is auto-detect)],
[case "${enableval}" in
  yes) amd_gpu=true ;;
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
    if $LSPCI -nn 2>/dev/null | awk '{ l=tolower($0); if ((match(l,/vga compatible controller/) || match(l,/3d controller/) || match(l,/display controller/) || match(l,/processing accelerators/)) && (match(l,/advanced micro devices/) || match(l,/ amd\/ati /))) found=1 } END { exit(found ? 0 : 1) }'; then
      amd_gpu=true
      amd_gpu_autodetect_result="amd-found"
    fi
  else
    amd_gpu_autodetect_result="lspci-missing"
  fi
fi

if test "x$amd_gpu" = "xtrue"; then
  AC_CHECK_HEADERS([gpu_performance_api/gpu_perf_api.h], [amd_gpu=true],
    [AC_MSG_ERROR([Unable to find gpu_performance_api/gpu_perf_api.h (GPUPerfAPI) for AMD GPU support])])
fi

if test "x$amd_gpu" = "xtrue"; then
  AC_MSG_NOTICE([AMD GPU summary: enabled (GPUPerfAPI path); detect=${amd_gpu_autodetect_result}])
else
  AC_MSG_NOTICE([AMD GPU summary: disabled; detect=${amd_gpu_autodetect_result}])
fi
AM_CONDITIONAL([AMD_GPU], [test x$amd_gpu = xtrue])

AC_ARG_ENABLE([opa],
[  --enable-opa		Enable Intel Omnipath Fabric support (default is false)],
[case "${enableval}" in
  yes) opa=true
  MONITOR_CFG_OPA_LIBS
  ;;
  no)  opa=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-opa]) ;;
esac],
[opa=false])
AM_CONDITIONAL([OPA], [test x$opa = xtrue])

AC_ARG_ENABLE([debug],
[  --enable-debug	Turn on debugging (default is false)],
[case "${enableval}" in
  yes) debug=true ;;     
  no)  debug=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-debug]) ;;
esac],
[debug=false])
AM_CONDITIONAL([DEBUG], [test x$debug = xtrue])

AC_SEARCH_LIBS([ev_run], [ev], [libev=yes], [AC_MSG_ERROR([Failed to find libev])])
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

AC_CONFIG_FILES([Makefile src/Makefile tests/Makefile])
AC_OUTPUT

