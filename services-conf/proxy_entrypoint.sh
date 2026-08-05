#!/bin/sh
# Proxy container entrypoint: generate OCSP resolver include, wait for SPA CSP
# policy files on the shared static volume, install them under /etc/nginx,
# strip non-web leftovers from the public frontend tree, validate nginx, exec.
set -eu

RESOLV_CONF="${HPCPERFSTATS_PROXY_RESOLV:-/etc/resolv.conf}"
RESOLVER_OUT="${HPCPERFSTATS_PROXY_RESOLVER_OUT:-/etc/nginx/nginx-resolver.inc}"
CSP_MACHINE="${HPCPERFSTATS_PROXY_CSP_MACHINE:-/srv/static/frontend/nginx-csp-machine.inc}"
CSP_PUB="${HPCPERFSTATS_PROXY_CSP_PUB:-/srv/static/frontend/nginx-csp-pub.inc}"
CSP_MACHINE_DST="${HPCPERFSTATS_PROXY_CSP_MACHINE_DST:-/etc/nginx/nginx-csp-machine.inc}"
CSP_PUB_DST="${HPCPERFSTATS_PROXY_CSP_PUB_DST:-/etc/nginx/nginx-csp-pub.inc}"
FRONTEND_STATIC_ROOT="${HPCPERFSTATS_PROXY_FRONTEND_STATIC:-/srv/static/frontend}"
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
  if grep -q "unsafe-inline" "${path}"; then
    echo "proxy_entrypoint: ${label} CSP include still allows unsafe-inline: ${path}" >&2
    return 1
  fi
  return 0
}

# Remove config/docs/source-map leftovers so nginx never serves them from /static/.
# Do not delete Next RSC *.txt payloads — those are required for client navigation.
strip_non_web_frontend_static() {
  root="$1"
  if [ ! -d "${root}" ]; then
    return 0
  fi
  find "${root}" -type f \( \
    -name '*.inc' -o \
    -name '*.md' -o \
    -name '*.markdown' -o \
    -name '*.map' -o \
    -name '*.example' -o \
    -name '*.sh' -o \
    -name '*.py' -o \
    -name '*.toml' -o \
    -name '*.ini' -o \
    -name '*.yml' -o \
    -name '*.yaml' \
  \) -delete
}

i=0
while [ "${i}" -lt "${CSP_WAIT_SECONDS}" ]; do
  if [ -f "${CSP_MACHINE}" ] && [ -f "${CSP_PUB}" ]; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

validate_csp_include "${CSP_MACHINE}" "machine"
validate_csp_include "${CSP_PUB}" "pub"

# Install CSP policies where nginx includes them; then strip non-web leftovers
# (including the volume CSP handoff files) from the public frontend tree.
cp "${CSP_MACHINE}" "${CSP_MACHINE_DST}"
cp "${CSP_PUB}" "${CSP_PUB_DST}"
strip_non_web_frontend_static "${FRONTEND_STATIC_ROOT}"

nginx -t
exec nginx -g "daemon off;"
