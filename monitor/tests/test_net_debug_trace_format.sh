#!/bin/sh
# Regression: net_iface_cache_each DEBUG TRACE must not use chained %s after %u
# (NET_FLAGS expansion drift caused vfprintf to read past varargs → SIGSEGV).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
net_c="${ROOT}/src/net.c"

test -f "${net_c}" || { echo "missing ${net_c}" >&2; exit 1; }

trace_line="$(awk '/^static void net_iface_cache_each/,/^}/ {
  if ($0 ~ /TRACE\(/) { print; exit }
}' "${net_c}")"

test -n "${trace_line}" \
  || { echo "net_iface_cache_each TRACE line not found in ${net_c}" >&2; exit 1; }

echo "${trace_line}" | grep -q 'flags 0x%x' \
  || { echo "net_iface_cache_each TRACE must use flags 0x%x format" >&2; exit 1; }

if echo "${trace_line}" | grep -q '%u%s'; then
  echo "net_iface_cache_each TRACE must not chain %s after %u (printf arg drift)" >&2
  exit 1
fi

if grep -q '#define NET_FLAGS' "${net_c}"; then
  echo "net.c must not define NET_FLAGS (removed with unsafe TRACE expansion)" >&2
  exit 1
fi

echo "test_net_debug_trace_format passed"
