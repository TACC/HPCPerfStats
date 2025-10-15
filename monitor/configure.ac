AC_INIT([hpcperfstats], [2.4], [rtevans@tacc.utexas.edu])
AM_INIT_AUTOMAKE([-Wall -Werror -Wno-portability])
AC_PROG_CC
AM_PROG_CC_C_O

AC_ARG_ENABLE([rabbitmq],
[  --enable-rabbitmq	Build the rabbitmq version (default is true)],
[case "${enableval}" in
  yes) AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
    [AC_MSG_ERROR([Unable to find librabbitmq.so!])])
    if test "x${have_rabbitmq}" = xyes; then
       AC_CHECK_HEADERS([amqp.h], [rabbitmq=true],
       [AC_MSG_ERROR([Unable to find amqp.h!])])
    fi
    ;;
  no)  rabbitmq=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-rabbitmq]) ;;
esac],
[AC_SEARCH_LIBS([amqp_new_connection], [rabbitmq], [have_rabbitmq=yes],
    [AC_MSG_ERROR([Unable to find librabbitmq.so!])])
    if test "x${have_rabbitmq}" = xyes; then
       AC_CHECK_HEADERS([amqp.h], [rabbitmq=true],
       [AC_MSG_ERROR([Unable to find amqp.h!])])
    fi
])
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
[  --enable-gpu		Enable Nvidia GPU support (default is false)],
[case "${enableval}" in
  yes) gpu=true
  AC_SEARCH_LIBS([nvmlInit], [nvidia-ml], [have_gpu=yes], 
  [AC_MSG_ERROR([Unable to find libnvidia-ml.so!])])
  if test "x${have_gpu}" = xyes; then 
     AC_CHECK_HEADERS([nvml.h], [gpu=true], 
     [AC_MSG_ERROR([Unable to find nvml.h on this system!])])  
  fi
  ;;
  no)  gpu=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-gpu]) ;;
esac],
[gpu=false])
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

