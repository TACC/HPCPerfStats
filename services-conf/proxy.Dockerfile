# syntax=docker/dockerfile:1
# TLS PEMs: materialized at container start from proxy_ssl_source settings volume
# via resolve_proxy_ssl_certs_dir.py in proxy_entrypoint.sh (not baked at build).
FROM alpine:3.24.1

# Pin nginx from Alpine edge main (stable packages lag); bump NGINX_EDGE_VERSION on proxy rebuilds.
ARG NGINX_EDGE_VERSION=1.30.4-r3
ARG ALPINE_EDGE_MAIN=https://dl-cdn.alpinelinux.org/alpine/edge/main

RUN apk add --no-cache \
    ca-certificates \
    netcat-openbsd \
    python3 \
 && apk add --no-cache \
    nginx=${NGINX_EDGE_VERSION} \
    nginx-mod-http-brotli=${NGINX_EDGE_VERSION} \
    --repository=${ALPINE_EDGE_MAIN} \
 && update-ca-certificates

RUN nginx -v

RUN mkdir -p /usr/local/lib/hpcperfstats-proxy /etc/nginx /etc/ssl/hpcperfstats

# Shared nginx snippets (static-files, edge headers, CSP, django-proxy-common) are
# compose bind-mounts only — do not COPY them here or they drift from mounts.
# Image ships: Python helpers, entrypoint, default.conf baseline, hosts include,
# and a placeholder OCSP resolver (entrypoint overwrites at start).
COPY services-conf/parse_hpcperfstats_proxy_hosts.py \
    services-conf/write_nginx_proxy_allowed_hosts_include.py \
    services-conf/write_nginx_resolver_include.py \
    services-conf/write_nginx_spa_csp_includes.py \
    services-conf/resolve_proxy_ssl_certs_dir.py \
    hpcperfstats/site/lib/spa_csp_meta.py \
    /usr/local/lib/hpcperfstats-proxy/

COPY services-conf/proxy_entrypoint.sh /usr/local/bin/proxy_entrypoint.sh

ENV PYTHONPATH=/usr/local/lib/hpcperfstats-proxy

RUN chmod 755 /usr/local/lib/hpcperfstats-proxy/write_nginx_proxy_allowed_hosts_include.py \
    /usr/local/lib/hpcperfstats-proxy/write_nginx_resolver_include.py \
    /usr/local/lib/hpcperfstats-proxy/write_nginx_spa_csp_includes.py \
    /usr/local/lib/hpcperfstats-proxy/resolve_proxy_ssl_certs_dir.py \
    /usr/local/bin/proxy_entrypoint.sh

WORKDIR /build

# Prefer the deployment ini when present in the build context; otherwise fall back to the example.
COPY hpcperfstats.ini /build/

# Committed services-conf/nginx.conf (fixed /etc/ssl/hpcperfstats TLS paths).
COPY services-conf/nginx.conf /build/nginx.conf

RUN set -eu; \
    if [ -f /build/hpcperfstats.ini ]; then INI=/build/hpcperfstats.ini; \
    elif [ -f /build/hpcperfstats.ini.example ]; then INI=/build/hpcperfstats.ini.example; \
    else echo "missing hpcperfstats.ini or hpcperfstats.ini.example in build context"; exit 1; \
    fi; \
    if [ ! -f /build/nginx.conf ]; then \
      echo "missing services-conf/nginx.conf in build context"; exit 1; \
    fi; \
    cp /build/nginx.conf /etc/nginx/http.d/default.conf; \
    python3 /usr/local/lib/hpcperfstats-proxy/write_nginx_proxy_allowed_hosts_include.py \
      --ini "${INI}" \
      --out /etc/nginx/hps-proxy-allowed-hosts.inc; \
    # Placeholder resolver so image-time inspection is possible; runtime entrypoint overwrites.
    printf '%s\n' \
      '# Placeholder replaced at container start by write_nginx_resolver_include.py' \
      'resolver 127.0.0.11 ipv6=off valid=300s;' \
      'resolver_timeout 5s;' \
      > /etc/nginx/nginx-resolver.inc; \
    rm -rf /build

STOPSIGNAL SIGTERM

CMD ["/usr/local/bin/proxy_entrypoint.sh"]
