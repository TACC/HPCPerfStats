AC_INIT([hpcperfstats], [2.4], [rtevans@tacc.utexas.edu])
AM_INIT_AUTOMAKE([foreign -Wall -Werror -Wno-portability])
AC_CANONICAL_HOST
AC_PROG_CC
AC_PROG_CXX
AM_PROG_CC_C_O

AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
    [AC_MSG_ERROR([Unable to find librabbitmq.so!])])
if test "x${have_rabbitmq}" = xyes; then
   AC_CHECK_HEADERS([amqp.h], [rabbitmq=true],
   [AC_MSG_ERROR([Unable to find amqp.h!])])
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

AC_SEARCH_LIBS([variorum_get_energy_json], [variorum], [have_variorum=yes],
  [AC_MSG_ERROR([Unable to find libvariorum.so!])])
if test "x${have_variorum}" = xyes; then
  AC_CHECK_HEADERS([variorum.h], [variorum=true],
    [AC_MSG_ERROR([Unable to find variorum.h!])])
fi
AM_CONDITIONAL([VARIORUM], [test x$variorum = xtrue])

AC_SEARCH_LIBS([perfmon_init], [likwid], [have_likwid=yes],
  [AC_MSG_ERROR([Unable to find liblikwid.so!])])
if test "x${have_likwid}" = xyes; then
  AC_CHECK_HEADERS([likwid.h], [likwid=true],
    [AC_MSG_ERROR([Unable to find likwid.h!])])
fi
AM_CONDITIONAL([LIKWID], [test x$likwid = xtrue])

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

if test "x$monitor_arch" = "xintel"; then
  MONITOR_ARCH_CPPFLAG="-DMONITOR_ARCH_INTEL"
else
  MONITOR_ARCH_CPPFLAG="-DMONITOR_ARCH_AMD"
fi
AC_SUBST([MONITOR_ARCH_CPPFLAG])
AM_CONDITIONAL([MONITOR_ARCH_INTEL], [test "x$monitor_arch" = "xintel"])
AM_CONDITIONAL([MONITOR_ARCH_AMD], [test "x$monitor_arch" = "xamd"])

AC_ARG_ENABLE([infiniband],
[  --enable-infiniband	Enable Infiniband support (default is true)],
[case "${enableval}" in
  yes) AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad], [have_ib=yes], 
    [AC_MSG_ERROR([Unable to find libibmad.so!])])
  if test "x${have_ib}" = xyes; then 
     AC_CHECK_HEADERS([infiniband/umad.h infiniband/mad.h], [infiniband=true],
     [AC_MSG_ERROR([Unable to find umad.h or mad.h on this system!])])  
  fi
  ;;
  no)  infiniband=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-infiniband]) ;;
esac],
[AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad], [have_ib=yes],
  [AC_MSG_ERROR([Unable to find libibmad.so!])])
  if test "x${have_ib}" = xyes; then 
     AC_CHECK_HEADERS([infiniband/umad.h infiniband/mad.h], [infiniband=true], 
     [AC_MSG_ERROR([Unable to find umad.h or mad.h on this system!])])  
  fi
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
  AC_SEARCH_LIBS([mic_open_device], [micmgmt], [have_mic=yes], 
  [AC_MSG_ERROR([Unable to find libmicmgmt.so!])])  
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
    if $LSPCI -nn 2>/dev/null | awk '{
      l=tolower($0);
      if ((index(l, "vga compatible controller") || index(l, "3d controller")) && index(l, "nvidia")) found=1
    } END { exit(found ? 0 : 1) }'; then
      gpu=true
      gpu_autodetect_result="nvidia-found"
    fi
  else
    gpu_autodetect_result="lspci-missing"
  fi
fi

if test "x$gpu" = "xtrue"; then
  AC_SEARCH_LIBS([dcgmInit], [dcgm], [have_gpu=yes],
    [AC_MSG_ERROR([Unable to find libdcgm.so!])])
  if test "x${have_gpu}" = xyes; then
    AC_CHECK_HEADERS([dcgm_agent.h dcgm_structs.h], [gpu=true],
      [AC_MSG_ERROR([Unable to find dcgm_agent.h/dcgm_structs.h on this system!])])
  fi
fi

if test "x$gpu" = "xtrue"; then
  AC_MSG_NOTICE([GPU summary: enabled (DCGM required); detect=${gpu_autodetect_result}])
else
  AC_MSG_NOTICE([GPU summary: disabled; detect=${gpu_autodetect_result}])
fi
AM_CONDITIONAL([GPU], [test x$gpu = xtrue])

AC_ARG_ENABLE([opa],
[  --enable-opa		Enable Intel Omnipath Fabric support (default is false)],
[case "${enableval}" in
  yes) opa=true
  AC_SEARCH_LIBS([iba_fstatus_msg], [public])
  AC_SEARCH_LIBS([oib_pa_client_init], [oib_utils], [have_opa=yes], 
  [AC_MSG_ERROR([Unable to find liboib_utils.a!])], [ -libmad -libumad -libverbs -lpublic -lpthread ])
  if test "x${have_opa}" = xyes; then 
     AC_CHECK_HEADERS([oib_utils.h], [opa=true], 
     [AC_MSG_ERROR([Unable to find oib_utils.h on this system!])])  
  fi
  AC_SEARCH_LIBS([ibv_create_cq], [ibverbs])
  AC_SEARCH_LIBS([umad_set_addr], [ibumad])
  AC_SEARCH_LIBS([mad_rpc_open_port], [ibmad])	
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

AC_SEARCH_LIBS([ev_run], [ev], [libev=yes], [AC_MSG_ERROR([Failed to find lib libev.so])])
AC_CHECK_HEADERS([ev.h], [ev=true], [AC_MSG_ERROR([Failed to find header ev.h])])

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

AC_CONFIG_FILES([Makefile src/Makefile])
AC_OUTPUT

