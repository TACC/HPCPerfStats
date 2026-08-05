#!/bin/sh
# Proxy container entrypoint: OCSP resolver; wait for SPA HTML on the read-only
# static volume; write hash CSP includes ONLY under /etc/nginx (never under
# /srv/static); validate; exec nginx.
set -eu

RESOLV_CONF="${HPCPERFSTATS_PROXY_RESOLV:-/etc/resolv.conf}"
RESOLVER_OUT="${HPCPERFSTATS_PROXY_RESOLVER_OUT:-/etc/nginx/nginx-resolver.inc}"
FRONTEND_STATIC_ROOT="${HPCPERFSTATS_PROXY_FRONTEND_STATIC:-/srv/static/frontend}"
CSP_OUT_DIR="${HPCPERFSTATS_PROXY_CSP_OUT_DIR:-/etc/nginx}"
CSP_MACHINE="${CSP_OUT_DIR}/nginx-csp-machine.inc"
CSP_PUB="${CSP_OUT_DIR}/nginx-csp-pub.inc"
MACHINE_HTML="${HPCPERFSTATS_PROXY_MACHINE_HTML:-${FRONTEND_STATIC_ROOT}/machine/index.html}"
PUB_HTML="${HPCPERFSTATS_PROXY_PUB_HTML:-${FRONTEND_STATIC_ROOT}/pub/index.html}"
CSP_WAIT_SECONDS="${HPCPERFSTATS_PROXY_CSP_WAIT_SECONDS:-120}"

python3 /usr/local/lib/hpcperfstats-proxy/write_nginx_resolver_include.py \
  --resolv "${RESOLV_CONF}" \
  --out "${RESOLVER_OUT}"

validate_csp_include() {
  path="$1"
  label="$2"
  if [ ! -f "${path}" ]; then
    echo "proxy_entrypoint: missing ${label} CSP include: ${path}" >&2
    return 1
  fi
  if ! grep -q "add_header Content-Security-Policy" "${path}"; then
    echo "proxy_entrypoint: ${label} CSP include lacks Content-Security-Policy: ${path}" >&2
    return 1
  fi
  # Bokeh SPA policies use style-src 'unsafe-inline' (path-justified). Never allow
  # script-src 'unsafe-inline' — that would undo the hash-based script contract.
  if grep -E "script-src[^;]*unsafe-inline" "${path}" >/dev/null 2>&1; then
    echo "proxy_entrypoint: ${label} CSP include allows script-src unsafe-inline: ${path}" >&2
    return 1
  fi
  # Refuse policies that still point browsers at a public static path.
  case "${path}" in
    /srv/static/*|/home/*/staticfiles/*)
      echo "proxy_entrypoint: refusing CSP include under public static tree: ${path}" >&2
      return 1
      ;;
  esac
  return 0
}

i=0
while [ "${i}" -lt "${CSP_WAIT_SECONDS}" ]; do
  if [ -f "${MACHINE_HTML}" ] && [ -f "${PUB_HTML}" ]; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ ! -f "${MACHINE_HTML}" ] || [ ! -f "${PUB_HTML}" ]; then
  echo "proxy_entrypoint: SPA HTML missing under ${FRONTEND_STATIC_ROOT} after ${CSP_WAIT_SECONDS}s" >&2
  exit 1
fi

# Hash CSP from HTML → private /etc/nginx only. Never write nginx config into /srv/static.
python3 /usr/local/lib/hpcperfstats-proxy/write_nginx_spa_csp_includes.py \
  --frontend-root "${FRONTEND_STATIC_ROOT}" \
  --out-dir "${CSP_OUT_DIR}"

validate_csp_include "${CSP_MACHINE}" "machine"
validate_csp_include "${CSP_PUB}" "pub"

nginx -t
exec nginx -g "daemon off;"
