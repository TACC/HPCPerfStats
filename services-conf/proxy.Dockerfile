# syntax=docker/dockerfile:1
# Source-built nginx for the Compose proxy image.
# Build on the CPU that will run the image (-march=native -mtune=native).
# TLS PEMs: materialized at container start from proxy_ssl_source settings volume
# via resolve_proxy_ssl_certs_dir.py in proxy_entrypoint.sh (not baked at build).
# Do not apk-install nginx / nginx-mod-http-brotli.

ARG ALPINE_VERSION=3.24.1

FROM alpine:${ALPINE_VERSION} AS proxy-build

ARG NGINX_VERSION=1.31.5
ARG NGINX_SHA256=e951607d534836624bd36b6b45a71dbfb055237deae3738da6bbf3270dada279
ARG OPENSSL_VERSION=3.5.7
ARG OPENSSL_SHA256=a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8
ARG JEMALLOC_VERSION=5.3.1
ARG JEMALLOC_SHA256=3826bc80232f22ed5c4662f3034f799ca316e819103bdc7bb99018a421706f92
ARG ZLIB_NG_VERSION=2.2.5
ARG ZLIB_NG_SHA256=5b3b022489f3ced82384f06db1e13ba148cbce38c7941e424d6cb414416acd18
ARG NGX_BROTLI_VERSION=1.0.0rc
ARG NGX_BROTLI_SHA256=c85cdcfd76703c95aa4204ee4c2e619aa5b075cac18f428202f65552104add3b
ARG BROTLI_VERSION=1.1.0
ARG BROTLI_SHA256=e720a6ca29428b803f4ad165371771f5398faba397edf6778837a18599ea13ff

ENV OPT_CFLAGS_LIBS="-O3 -march=native -mtune=native -flto -g0"

RUN apk add --no-cache \
    build-base \
    cmake \
    curl \
    linux-headers \
    pcre2-dev \
    perl

# Print out compiler platform detection

RUN gcc -march=native -mtune=native -Q --help=target

# --- jemalloc ---
RUN set -eux; \
  curl -fsSL "https://github.com/jemalloc/jemalloc/releases/download/${JEMALLOC_VERSION}/jemalloc-${JEMALLOC_VERSION}.tar.bz2" \
    -o /tmp/jemalloc.tar.bz2; \
  echo "${JEMALLOC_SHA256}  /tmp/jemalloc.tar.bz2" | sha256sum -c -; \
  mkdir -p /usr/src/jemalloc; \
  tar -xjf /tmp/jemalloc.tar.bz2 -C /usr/src/jemalloc --strip-components=1; \
  cd /usr/src/jemalloc; \
  CFLAGS="${OPT_CFLAGS_LIBS}" CXXFLAGS="${OPT_CFLAGS_LIBS}" \
    ./configure --prefix=/opt/jemalloc; \
  make -j"$(nproc)"; \
  make install; \
  rm -rf /usr/src/jemalloc /tmp/jemalloc.tar.bz2

WORKDIR /usr/src

# --- zlib-ng (tree for nginx --with-zlib; ZLIB_COMPAT ABI) ---
# nginx auto/lib/zlib injects --with-zlib-opt into CFLAGS when it re-runs
# ./configure. zlib-ng requires --zlib-compat as a configure argv flag; passing
# it via CFLAGS aborts with "Compiler error reporting is too harsh".
RUN set -eux; \
  curl -fsSL "https://github.com/zlib-ng/zlib-ng/archive/refs/tags/${ZLIB_NG_VERSION}.tar.gz" \
    -o /tmp/zlib-ng.tar.gz; \
  echo "${ZLIB_NG_SHA256}  /tmp/zlib-ng.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/zlib-ng; \
  tar -xzf /tmp/zlib-ng.tar.gz -C /usr/src/zlib-ng --strip-components=1; \
  cd /usr/src/zlib-ng; \
  mv configure configure.zlib-ng; \
  printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'extra=""' \
    'new_cflags=""' \
    'for arg in ${CFLAGS:-}; do' \
    '  case "$arg" in' \
    '    --zlib-compat) extra="$extra --zlib-compat" ;;' \
    '    *) new_cflags="${new_cflags:+$new_cflags }$arg" ;;' \
    '  esac' \
    'done' \
    'export CFLAGS="$new_cflags"' \
    'exec ./configure.zlib-ng --zlib-compat "$@" $extra' \
    > configure; \
  chmod +x configure; \
  CFLAGS="--zlib-compat -pipe" CC=cc ./configure; \
  test -f Makefile; \
  make distclean; \
  rm -f /tmp/zlib-ng.tar.gz

# --- OpenSSL (nginx --with-openssl builds it; no-shared folds into nginx) ---
RUN set -eux; \
  curl -fsSL "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" \
    -o /tmp/openssl.tar.gz; \
  echo "${OPENSSL_SHA256}  /tmp/openssl.tar.gz" | sha256sum -c -; \
  tar -xzf /tmp/openssl.tar.gz -C /usr/src; \
  test -d "/usr/src/openssl-${OPENSSL_VERSION}"; \
  rm -f /tmp/openssl.tar.gz

# --- ngx_brotli (+ brotli sources into empty submodule dir) ---
# brotli >=1.1 dropped scripts/sources.lst; ngx_brotli v1.0.0rc then links
# -lbrotlienc instead of compiling deps sources. Pre-build static libs to
# /opt/brotli so the nginx link line can resolve them (folded into the binary).
RUN set -eux; \
  curl -fsSL "https://github.com/google/ngx_brotli/archive/refs/tags/v${NGX_BROTLI_VERSION}.tar.gz" \
    -o /tmp/ngx_brotli.tar.gz; \
  echo "${NGX_BROTLI_SHA256}  /tmp/ngx_brotli.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/ngx_brotli; \
  tar -xzf /tmp/ngx_brotli.tar.gz -C /usr/src/ngx_brotli --strip-components=1; \
  curl -fsSL "https://github.com/google/brotli/archive/refs/tags/v${BROTLI_VERSION}.tar.gz" \
    -o /tmp/brotli.tar.gz; \
  echo "${BROTLI_SHA256}  /tmp/brotli.tar.gz" | sha256sum -c -; \
  rm -rf /usr/src/ngx_brotli/deps/brotli; \
  mkdir -p /usr/src/ngx_brotli/deps; \
  tar -xzf /tmp/brotli.tar.gz -C /usr/src/ngx_brotli/deps; \
  mv "/usr/src/ngx_brotli/deps/brotli-${BROTLI_VERSION}" /usr/src/ngx_brotli/deps/brotli; \
  test -f /usr/src/ngx_brotli/deps/brotli/c/include/brotli/decode.h; \
  cmake -S /usr/src/ngx_brotli/deps/brotli -B /usr/src/ngx_brotli/deps/brotli/out \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_INSTALL_PREFIX=/opt/brotli \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_C_FLAGS="${OPT_CFLAGS_LIBS}" \
    -DCMAKE_CXX_FLAGS="${OPT_CFLAGS_LIBS}"; \
  cmake --build /usr/src/ngx_brotli/deps/brotli/out -j"$(nproc)"; \
  cmake --install /usr/src/ngx_brotli/deps/brotli/out; \
  test -f /opt/brotli/lib/libbrotlienc.a; \
  test -f /opt/brotli/lib/libbrotlidec.a; \
  test -f /opt/brotli/lib/libbrotlicommon.a; \
  rm -f /tmp/ngx_brotli.tar.gz /tmp/brotli.tar.gz

# --- nginx ---
RUN set -eux; \
  curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
    -o /tmp/nginx.tar.gz; \
  echo "${NGINX_SHA256}  /tmp/nginx.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/nginx; \
  tar -xzf /tmp/nginx.tar.gz -C /usr/src/nginx --strip-components=1; \
  cd /usr/src/nginx; \
  ./configure \
    --prefix=/opt/nginx \
    --sbin-path=/usr/sbin/nginx \
    --conf-path=/etc/nginx/nginx.conf \
    --pid-path=/run/nginx/nginx.pid \
    --lock-path=/run/nginx/nginx.lock \
    --error-log-path=/var/log/nginx/error.log \
    --http-log-path=/var/log/nginx/access.log \
    --http-client-body-temp-path=/var/lib/nginx/tmp/client_body \
    --http-proxy-temp-path=/var/lib/nginx/tmp/proxy \
    --http-fastcgi-temp-path=/var/lib/nginx/tmp/fastcgi \
    --http-uwsgi-temp-path=/var/lib/nginx/tmp/uwsgi \
    --http-scgi-temp-path=/var/lib/nginx/tmp/scgi \
    --user=nginx \
    --group=nginx \
    --with-http_ssl_module \
    --with-http_v2_module \
    --with-http_gzip_static_module \
    --with-http_stub_status_module \
    --with-pcre \
    --add-module=../ngx_brotli \
    --with-openssl=../openssl-${OPENSSL_VERSION} \
    --with-zlib=../zlib-ng \
    --with-cc-opt="${OPT_CFLAGS_LIBS}" \
    --with-ld-opt="-flto -L/opt/jemalloc/lib -L/opt/brotli/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,--no-as-needed -ljemalloc -lbrotlienc -lbrotlidec -lbrotlicommon" \
    --with-openssl-opt="no-nextprotoneg no-weak-ssl-ciphers no-ssl3 no-shared enable-ec_nistp_64_gcc_128" \
    --with-zlib-opt="--zlib-compat"; \
  make -j"$(nproc)"; \
  make install; \
  strip --strip-unneeded /usr/sbin/nginx; \
  # --conf-path=/etc/nginx/nginx.conf installs mime.types under /etc/nginx/, not
  # prefix/conf/. Normalize into /opt/nginx/conf for the runtime COPY.
  mkdir -p /opt/nginx/conf; \
  if [ -f /etc/nginx/mime.types ]; then \
    cp /etc/nginx/mime.types /opt/nginx/conf/mime.types; \
  elif [ -f /opt/nginx/conf/mime.types ]; then \
    :; \
  else \
    echo "mime.types missing after make install"; ls -la /etc/nginx /opt/nginx/conf || true; exit 1; \
  fi; \
  test -f /opt/nginx/conf/mime.types; \
  nginx -V 2>&1 | tee /tmp/nginx-V.txt; \
  grep -F "nginx/${NGINX_VERSION}" /tmp/nginx-V.txt; \
  grep -F -- "-mtune=native" /tmp/nginx-V.txt; \
  grep -F -- "-ljemalloc" /tmp/nginx-V.txt; \
  grep -F -- "--with-zlib-opt=--zlib-compat" /tmp/nginx-V.txt || \
    grep -F -- '--with-zlib-opt="--zlib-compat"' /tmp/nginx-V.txt; \
  grep -Fi openssl /tmp/nginx-V.txt; \
  grep -Fi brotli /tmp/nginx-V.txt; \
  ldd /usr/sbin/nginx | tee /tmp/nginx-ldd.txt; \
  grep -F /opt/jemalloc /tmp/nginx-ldd.txt; \
  rm -rf /usr/src /tmp/nginx.tar.gz /tmp/nginx-V.txt /tmp/nginx-ldd.txt; \
  if [ -e /usr/src ]; then echo "leftover /usr/src"; exit 1; fi

# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION}

ARG NGINX_VERSION=1.31.5

RUN apk add --no-cache \
    ca-certificates \
    libstdc++ \
    netcat-openbsd \
    pcre2 \
    python3 \
 && update-ca-certificates \
 && addgroup -S nginx \
 && adduser -S -D -H -h /var/cache/nginx -s /sbin/nologin -G nginx nginx \
 && mkdir -p \
    /etc/nginx/http.d \
    /etc/ssl/hpcperfstats \
    /run/nginx \
    /var/log/nginx \
    /var/lib/nginx/tmp/client_body \
    /var/lib/nginx/tmp/proxy \
    /var/lib/nginx/tmp/fastcgi \
    /var/lib/nginx/tmp/uwsgi \
    /var/lib/nginx/tmp/scgi \
    /usr/local/lib/hpcperfstats-proxy \
 && chown -R nginx:nginx /var/log/nginx /var/lib/nginx /run/nginx

COPY --from=proxy-build /opt/jemalloc /opt/jemalloc
COPY --from=proxy-build /opt/nginx /opt/nginx
COPY --from=proxy-build /usr/sbin/nginx /usr/sbin/nginx

RUN set -eux; \
  test -x /usr/sbin/nginx; \
  test -f /opt/nginx/conf/mime.types; \
  cp /opt/nginx/conf/mime.types /etc/nginx/mime.types; \
  # jemalloc make install already ships libjemalloc.so.2; do not ln -sf it onto itself.
  test -e /opt/jemalloc/lib/libjemalloc.so.2; \
  ls -la /opt/jemalloc/lib/libjemalloc.so*; \
  nginx -V 2>&1 | tee /tmp/nginx-V-runtime.txt; \
  grep -F "nginx/${NGINX_VERSION}" /tmp/nginx-V-runtime.txt; \
  ldd /usr/sbin/nginx | tee /tmp/nginx-ldd-runtime.txt; \
  grep -F /opt/jemalloc /tmp/nginx-ldd-runtime.txt; \
  # Fail closed: never ship apk nginx packages.
  if apk info -e nginx >/dev/null 2>&1; then echo "apk nginx present"; exit 1; fi; \
  if apk info -e nginx-mod-http-brotli >/dev/null 2>&1; then echo "apk brotli mod present"; exit 1; fi; \
  rm -f /tmp/nginx-V-runtime.txt /tmp/nginx-ldd-runtime.txt

ENV LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2
ENV LD_LIBRARY_PATH=/opt/jemalloc/lib

# Shared nginx snippets (static-files, edge headers, CSP, django-proxy-common) are
# compose bind-mounts only — do not COPY them here or they drift from mounts.
# Image ships: Python helpers, entrypoint, default.conf baseline, hosts include,
# main nginx.conf baseline, and a placeholder OCSP resolver (entrypoint overwrites).
COPY services-conf/parse_hpcperfstats_proxy_hosts.py \
    services-conf/write_nginx_proxy_allowed_hosts_include.py \
    services-conf/write_nginx_resolver_include.py \
    services-conf/write_nginx_spa_csp_includes.py \
    services-conf/resolve_proxy_ssl_certs_dir.py \
    hpcperfstats/site/lib/spa_csp_meta.py \
    /usr/local/lib/hpcperfstats-proxy/

COPY services-conf/proxy_entrypoint.sh /usr/local/bin/proxy_entrypoint.sh
COPY services-conf/nginx-main.conf /etc/nginx/nginx.conf

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
    # Fail closed: no Control API / early-body surfaces in committed vhost.
    if grep -E '(^|[[:space:]])api[[:space:]]*;|client_body_early_read' /build/nginx.conf /etc/nginx/nginx.conf >/dev/null; then \
      echo "forbidden Control API / early-body directives in nginx conf"; exit 1; \
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
