#!/bin/sh
# Regression: static librabbitmq probe needs -lrt; cmake installs under lib/; prepare guards archives.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
bundle="${ROOT}/scripts/build_static_bundle.sh"
cross="${ROOT}/scripts/cross_compile_test.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

# Autoconf: other-libraries fallback before hard error (mirror libev / Libs.private).
grep -q 'amqp_new_connection' "${ac}" \
  || { echo "configure.ac must AC_SEARCH_LIBS amqp_new_connection" >&2; exit 1; }
grep -q -- '[-]lrt -pthread' "${ac}" \
  || { echo "configure.ac rabbitmq probe must try -lrt -pthread (static Libs.private)" >&2; exit 1; }
# Ensure -lrt is attached to the amqp probe (not only elsewhere).
awk '
  /AC_SEARCH_LIBS\(\[amqp_new_connection\]/ { in_amqp=1 }
  in_amqp && /-lrt -pthread/ { found=1; exit }
  in_amqp && /AC_SEARCH_LIBS\(/ && !/amqp_new_connection/ { in_amqp=0 }
  END { exit(found ? 0 : 1) }
' "${ac}" \
  || { echo "configure.ac: -lrt -pthread must be on amqp_new_connection AC_SEARCH_LIBS" >&2; exit 1; }

# Native + foreign rabbitmq-c: force lib/ (not lib64-only GNUInstallDirs).
grep -q 'CMAKE_INSTALL_LIBDIR=lib' "${bundle}" \
  || { echo "build_static_bundle.sh must pass -DCMAKE_INSTALL_LIBDIR=lib" >&2; exit 1; }
grep -q 'lib/librabbitmq.a' "${bundle}" \
  || { echo "build_static_bundle.sh must assert PREFIX/lib/librabbitmq.a after install" >&2; exit 1; }
grep -q 'CMAKE_INSTALL_LIBDIR=lib' "${cross}" \
  || { echo "cross_compile_test.sh must pass -DCMAKE_INSTALL_LIBDIR=lib for rabbitmq-c" >&2; exit 1; }

# prepare: fail before make dist if core static deps missing (all arches).
grep -q 'libev.a' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must guard libev.a" >&2; exit 1; }
grep -q 'librabbitmq.a' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must guard librabbitmq.a" >&2; exit 1; }

echo "test_configure_rabbitmq_static_probe passed"
