# syntax=docker/dockerfile:1
# Source-built nginx for the Compose proxy image.
# Build on the CPU that will run the image (-march=native -mtune=native).
# TLS PEMs: materialized at container start from proxy_ssl_source settings volume
# via resolve_proxy_ssl_certs_dir.py in proxy_entrypoint.sh (not baked at build).
# Do not apk-install nginx / nginx-mod-http-brotli.

ARG ALPINE_VERSION=3.24.2

FROM alpine:${ALPINE_VERSION} AS proxy-build

ARG PGO_PHASE=skip
ARG PGO_ROOT=/root/.hpcperfstats_pgo
ENV HPC_PGO_PHASE=${PGO_PHASE} HPC_PGO_ROOT=${PGO_ROOT}
ENV CC=clang CXX=clang++ LD=ld.lld

COPY services-conf/pgo_clang_flags.sh /usr/local/lib/hpcperfstats/pgo_clang_flags.sh
COPY services-conf/clang_march_native_probe.sh /usr/local/lib/hpcperfstats/clang_march_native_probe.sh
COPY services-conf/alpine_pgo_profile_runtime_link.sh /usr/local/lib/hpcperfstats/alpine_pgo_profile_runtime_link.sh
RUN chmod +x /usr/local/lib/hpcperfstats/pgo_clang_flags.sh /usr/local/lib/hpcperfstats/clang_march_native_probe.sh /usr/local/lib/hpcperfstats/alpine_pgo_profile_runtime_link.sh

# nginx and /opt deps: unified -O2 ThinLTO bake (+ optional PGO via pgo_clang_flags.sh).
ARG NGINX_VERSION=1.31.6
ARG NGINX_SHA256=974ed5298a5e398e008704ed5db284e655fc270c596493dbccada452448fc9f1
ARG OPENSSL_VERSION=3.5.9
ARG OPENSSL_SHA256=603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
ARG JEMALLOC_VERSION=5.4.0
ARG JEMALLOC_SHA256=200776fac271093e7c2f21edd6d62657ecd2be578d9328633f2a86bfa6ef4f1d
ARG ZLIB_NG_VERSION=2.3.3
ARG ZLIB_NG_SHA256=f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1
ARG NGX_BROTLI_VERSION=1.0.0rc
ARG NGX_BROTLI_SHA256=c85cdcfd76703c95aa4204ee4c2e619aa5b075cac18f428202f65552104add3b
ARG BROTLI_VERSION=1.2.0
ARG BROTLI_SHA256=816c96e8e8f193b40151dad7e8ff37b1221d019dbcb9c35cd3fadbfe6477dfec
ARG ZSTD_VERSION=1.5.7
ARG ZSTD_SHA256=eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3
ARG ZSTD_NGINX_MODULE_VERSION=0.2.2
ARG ZSTD_NGINX_MODULE_SHA256=d4db8937f035ebb5e7efca833492611f8f5e4f710dbd3fbdd2f1aa5a85d3fe5e

# Quoted heredoc (<<'BASH') disables BuildKit host $(…) expansion; pins resolve from ENV at RUN time.
ENV NGINX_VERSION=${NGINX_VERSION} \
    NGINX_SHA256=${NGINX_SHA256} \
    OPENSSL_VERSION=${OPENSSL_VERSION} \
    OPENSSL_SHA256=${OPENSSL_SHA256} \
    JEMALLOC_VERSION=${JEMALLOC_VERSION} \
    JEMALLOC_SHA256=${JEMALLOC_SHA256} \
    ZLIB_NG_VERSION=${ZLIB_NG_VERSION} \
    ZLIB_NG_SHA256=${ZLIB_NG_SHA256} \
    NGX_BROTLI_VERSION=${NGX_BROTLI_VERSION} \
    NGX_BROTLI_SHA256=${NGX_BROTLI_SHA256} \
    BROTLI_VERSION=${BROTLI_VERSION} \
    BROTLI_SHA256=${BROTLI_SHA256} \
    ZSTD_VERSION=${ZSTD_VERSION} \
    ZSTD_SHA256=${ZSTD_SHA256} \
    ZSTD_NGINX_MODULE_VERSION=${ZSTD_NGINX_MODULE_VERSION} \
    ZSTD_NGINX_MODULE_SHA256=${ZSTD_NGINX_MODULE_SHA256}

ENV OPT_CFLAGS_LIBS="-O2 -march=native -mtune=native -flto=thin --ld-path=/usr/bin/ld.lld -g0"

RUN apk add --no-cache \
    bash \
    clang22 \
    compiler-rt \
    lld22 \
    llvm22-dev \
    build-base \
    bzip2 \
    cmake \
    curl \
    linux-headers \
    pcre2-dev \
    perl

RUN test -x /usr/bin/ld.lld && /bin/bash /usr/local/lib/hpcperfstats/alpine_pgo_profile_runtime_link.sh

# Podman OCI ignores Dockerfile SHELL; PGO RUN steps invoke /bin/bash explicitly (BASH_ENV sources pgo_clang_flags.sh).
ENV BASH_ENV=/usr/local/lib/hpcperfstats/pgo_clang_flags.sh
ENV HPC_ALPINE_LLVM_TOOLCHAIN=1 \
  AR=/usr/lib/llvm22/bin/llvm-ar \
  RANLIB=/usr/lib/llvm22/bin/llvm-ranlib \
  NM=/usr/lib/llvm22/bin/llvm-nm \
  LLVM_CONFIG=/usr/lib/llvm22/bin/llvm-config

# Print out compiler platform detection (clang_march_native_probe.sh).
RUN /bin/bash -o pipefail /usr/local/lib/hpcperfstats/clang_march_native_probe.sh

# --- jemalloc ---
ARG PGO_NAMESPACE=proxy/jemalloc
RUN --mount=type=bind,from=pgo,source=.,target=/root/.hpcperfstats_pgo,rw \
  /bin/bash -o pipefail -s <<'BASH'
set -eux
curl -fsSL "https://github.com/jemalloc/jemalloc/releases/download/${JEMALLOC_VERSION}/jemalloc-${JEMALLOC_VERSION}.tar.bz2" \
  -o /tmp/jemalloc.tar.bz2
echo "${JEMALLOC_SHA256}  /tmp/jemalloc.tar.bz2" | sha256sum -c -
mkdir -p /usr/src/jemalloc
tar -xjf /tmp/jemalloc.tar.bz2 -C /usr/src/jemalloc --strip-components=1
cd /usr/src/jemalloc
_cf_cfg="$(hpcperfstats_configure_cflags proxy/jemalloc)"
_cf_bake="$(hpcperfstats_bake_cflags proxy/jemalloc)"
_lto_ld="$(hpcperfstats_alpine_thinlto_ldflags)"
hpcperfstats_alpine_jemalloc_export_link_toolchain
AR="${AR}" RANLIB="${RANLIB}" NM="${NM}" CC="${CC}" CXX="${CXX}" \
  CPPFLAGS="-D_GNU_SOURCE" CFLAGS="${_cf_cfg}" CXXFLAGS="${_cf_cfg}" \
  ./configure --prefix=/opt/jemalloc
make -j"$(nproc)" CC="${CC}" CXX="${CXX}" CFLAGS="${_cf_bake}" CXXFLAGS="${_cf_bake}" LDFLAGS="${_lto_ld}" EXTRA_LDFLAGS="${_lto_ld}" \
  AR="${AR}" RANLIB="${RANLIB}" NM="${NM}"
make install CC="${CC}" CXX="${CXX}" CFLAGS="${_cf_bake}" CXXFLAGS="${_cf_bake}" LDFLAGS="${_lto_ld}" EXTRA_LDFLAGS="${_lto_ld}" \
  AR="${AR}" RANLIB="${RANLIB}" NM="${NM}"
rm -rf /usr/src/jemalloc /tmp/jemalloc.tar.bz2
BASH

WORKDIR /usr/src

# --- zlib-ng (tree for nginx --with-zlib; ZLIB_COMPAT ABI) ---
# nginx auto/lib/zlib injects --with-zlib-opt into CFLAGS when it re-runs
# ./configure. zlib-ng requires --zlib-compat as a configure argv flag; passing
# it via CFLAGS aborts with "Compiler error reporting is too harsh".
ARG PGO_NAMESPACE=proxy/zlib-ng
RUN --mount=type=bind,from=pgo,source=.,target=/root/.hpcperfstats_pgo,rw \
  /bin/bash -o pipefail -s <<'BASH'
set -eux
_cf_cfg="$(hpcperfstats_configure_cflags proxy/zlib-ng)"
_cf_bake="$(hpcperfstats_bake_cflags proxy/zlib-ng)"
curl -fsSL "https://github.com/zlib-ng/zlib-ng/archive/refs/tags/${ZLIB_NG_VERSION}.tar.gz" \
  -o /tmp/zlib-ng.tar.gz
echo "${ZLIB_NG_SHA256}  /tmp/zlib-ng.tar.gz" | sha256sum -c -
mkdir -p /usr/src/zlib-ng
tar -xzf /tmp/zlib-ng.tar.gz -C /usr/src/zlib-ng --strip-components=1
cd /usr/src/zlib-ng
mv configure configure.zlib-ng
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
  > configure
chmod +x configure
CFLAGS="${_cf_cfg}" CC=clang ./configure --zlib-compat
test -f Makefile
# make … CFLAGS= overrides Makefile flags; keep -DZLIB_COMPAT from ./configure --zlib-compat.
make -j"$(nproc)" libz.a CC=clang CFLAGS="${_cf_bake} -DZLIB_COMPAT"
test -f libz.a
rm -f /tmp/zlib-ng.tar.gz
BASH

# --- OpenSSL (nginx --with-openssl builds it; no-shared folds into nginx) ---
RUN set -eux; \
  curl -fsSL "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" \
    -o /tmp/openssl.tar.gz; \
  echo "${OPENSSL_SHA256}  /tmp/openssl.tar.gz" | sha256sum -c -; \
  tar -xzf /tmp/openssl.tar.gz -C /usr/src; \
  test -d "/usr/src/openssl-${OPENSSL_VERSION}"; \
  rm -f /tmp/openssl.tar.gz

# --- ngx_brotli (+ brotli sources into empty submodule dir) ---
# brotli cmake: clang + llvm-ar/ranlib + lld (ThinLTO); build CLI (default tools ON).
# brotli >=1.1 dropped scripts/sources.lst; ngx_brotli v1.0.0rc then links
# -lbrotlienc instead of compiling deps sources. Pre-build static libs to
# /opt/brotli so the nginx link line can resolve them (folded into the binary).
ARG PGO_NAMESPACE=proxy/brotli
RUN --mount=type=bind,from=pgo,source=.,target=/root/.hpcperfstats_pgo,rw \
  /bin/bash -o pipefail -s <<'BASH'
set -eux
_cflags="$(hpcperfstats_bake_cflags proxy/brotli)"
_lto_ld="$(hpcperfstats_alpine_thinlto_ldflags)"
curl -fsSL "https://github.com/google/ngx_brotli/archive/refs/tags/v${NGX_BROTLI_VERSION}.tar.gz" \
  -o /tmp/ngx_brotli.tar.gz
echo "${NGX_BROTLI_SHA256}  /tmp/ngx_brotli.tar.gz" | sha256sum -c -
mkdir -p /usr/src/ngx_brotli
tar -xzf /tmp/ngx_brotli.tar.gz -C /usr/src/ngx_brotli --strip-components=1
curl -fsSL "https://github.com/google/brotli/archive/refs/tags/v${BROTLI_VERSION}.tar.gz" \
  -o /tmp/brotli.tar.gz
echo "${BROTLI_SHA256}  /tmp/brotli.tar.gz" | sha256sum -c -
rm -rf /usr/src/ngx_brotli/deps/brotli
mkdir -p /usr/src/ngx_brotli/deps
tar -xzf /tmp/brotli.tar.gz -C /usr/src/ngx_brotli/deps
mv "/usr/src/ngx_brotli/deps/brotli-${BROTLI_VERSION}" /usr/src/ngx_brotli/deps/brotli
test -f /usr/src/ngx_brotli/deps/brotli/c/include/brotli/decode.h
cmake -S /usr/src/ngx_brotli/deps/brotli -B /usr/src/ngx_brotli/deps/brotli/out \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_AR=/usr/lib/llvm22/bin/llvm-ar \
  -DCMAKE_RANLIB=/usr/lib/llvm22/bin/llvm-ranlib \
  -DCMAKE_EXE_LINKER_FLAGS="${_lto_ld}" \
  -DCMAKE_INSTALL_PREFIX=/opt/brotli \
  -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_C_FLAGS="${_cflags}" \
  -DCMAKE_CXX_FLAGS="${_cflags}"
cmake --build /usr/src/ngx_brotli/deps/brotli/out -j"$(nproc)"
cmake --install /usr/src/ngx_brotli/deps/brotli/out
test -f /opt/brotli/lib/libbrotlienc.a
test -f /opt/brotli/lib/libbrotlidec.a
test -f /opt/brotli/lib/libbrotlicommon.a
test -x /opt/brotli/bin/brotli
rm -f /tmp/ngx_brotli.tar.gz /tmp/brotli.tar.gz
BASH

# --- zstd (static libzstd.a) + GetPageSpeed zstd-nginx-module (tokers fork) ---
# Proxy has no /opt/zlib-ng or /opt/lz4; do not enable HAVE_ZLIB/HAVE_LZ4 (those
# are for the db/Python zstd CLI). Do not lib-mt: nginx already has one worker
# per CPU. Fold libzstd.a into nginx; do not COPY /opt/zstd into the runtime.
ARG PGO_NAMESPACE=proxy/zstd
RUN --mount=type=bind,from=pgo,source=.,target=/root/.hpcperfstats_pgo,rw \
  /bin/bash -o pipefail -s <<'BASH'
set -eux
_cflags="$(hpcperfstats_bake_cflags proxy/zstd)"
curl -fsSL "https://github.com/facebook/zstd/releases/download/v${ZSTD_VERSION}/zstd-${ZSTD_VERSION}.tar.gz" \
  -o /tmp/zstd.tar.gz
echo "${ZSTD_SHA256}  /tmp/zstd.tar.gz" | sha256sum -c -
mkdir -p /usr/src/zstd
tar -xzf /tmp/zstd.tar.gz -C /usr/src/zstd --strip-components=1
make -j"$(nproc)" -C /usr/src/zstd/lib libzstd.a \
  PREFIX=/opt/zstd \
  MOREFLAGS="${_cflags}" \
  ZSTD_LEGACY_SUPPORT=0 \
  HAVE_ZLIB=0 \
  HAVE_LZ4=0
make -C /usr/src/zstd/lib install-static PREFIX=/opt/zstd
make -C /usr/src/zstd/lib install-includes PREFIX=/opt/zstd
test -f /opt/zstd/lib/libzstd.a
test -f /opt/zstd/include/zstd.h
curl -fsSL "https://github.com/GetPageSpeed/zstd-nginx-module/archive/refs/tags/${ZSTD_NGINX_MODULE_VERSION}.tar.gz" \
  -o /tmp/zstd-nginx-module.tar.gz
echo "${ZSTD_NGINX_MODULE_SHA256}  /tmp/zstd-nginx-module.tar.gz" | sha256sum -c -
mkdir -p /usr/src/zstd-nginx-module
tar -xzf /tmp/zstd-nginx-module.tar.gz -C /usr/src/zstd-nginx-module --strip-components=1
test -f /usr/src/zstd-nginx-module/config
rm -rf /usr/src/zstd /tmp/zstd.tar.gz /tmp/zstd-nginx-module.tar.gz
BASH

# --- nginx ---
ARG PGO_NAMESPACE=proxy/nginx
RUN --mount=type=bind,from=pgo,source=.,target=/root/.hpcperfstats_pgo,rw \
  /bin/bash -o pipefail -s <<'BASH'
set -eux
hpcperfstats_pgo_ensure_proxy_nginx_link_dirs
_cflags="$(hpcperfstats_bake_cflags proxy/nginx)"
_openssl_cflags="$(hpcperfstats_bake_cflags proxy/openssl)"
test -f /usr/src/zlib-ng/libz.a
curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
  -o /tmp/nginx.tar.gz
echo "${NGINX_SHA256}  /tmp/nginx.tar.gz" | sha256sum -c -
mkdir -p /usr/src/nginx
tar -xzf /tmp/nginx.tar.gz -C /usr/src/nginx --strip-components=1
cd /usr/src/nginx
export ZSTD_INC=/opt/zstd/include
export ZSTD_LIB=/opt/zstd/lib
nginx_bake_cflags="${_cflags} -I/opt/zstd/include -DZLIB_COMPAT"
# libzstd.a via zstd-nginx-module. Same ThinLTO flags at configure and make.
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
  --add-module=../zstd-nginx-module \
  --with-openssl=../openssl-${OPENSSL_VERSION} \
  --with-zlib=../zlib-ng \
  --with-cc-opt="${_cflags} -I/opt/zstd/include" \
  --with-ld-opt="-L/opt/jemalloc/lib -L/opt/brotli/lib -Wl,-rpath,/opt/jemalloc/lib -ljemalloc -lbrotlienc -lbrotlidec -lbrotlicommon -lstdc++ --ld-path=/usr/bin/ld.lld" \
  --with-openssl-opt="no-nextprotoneg no-weak-ssl-ciphers no-ssl3 no-shared no-fuzz-afl no-fuzz-libfuzzer no-tests no-apps enable-ec_nistp_64_gcc_128 ${_openssl_cflags}" \
  --with-zlib-opt="--zlib-compat"
# objs/Makefile builds ../openssl-* and ../zlib-ng in parallel; zlib-ng distclean races
# zlib-ng.h generation. OpenSSL tests/apps/fuzz add hundreds of ThinLTO links.
_lto_ld="$(hpcperfstats_alpine_thinlto_ldflags)"
export AR=/usr/lib/llvm22/bin/llvm-ar RANLIB=/usr/lib/llvm22/bin/llvm-ranlib
hpcperfstats_alpine_jemalloc_export_link_toolchain
make -f objs/Makefile -j1 \
  "../openssl-${OPENSSL_VERSION}/.openssl/include/openssl/ssl.h" \
  ../zlib-ng/libz.a \
  CFLAGS="${nginx_bake_cflags}" LDFLAGS="${_lto_ld}" EXTRA_LDFLAGS="${_lto_ld}"
make -j"$(nproc)" CFLAGS="${nginx_bake_cflags}"
make install CFLAGS="${nginx_bake_cflags}"
strip --strip-unneeded /usr/sbin/nginx
# --conf-path=/etc/nginx/nginx.conf installs mime.types under /etc/nginx/, not
# prefix/conf/. Normalize into /opt/nginx/conf for the runtime COPY.
mkdir -p /opt/nginx/conf
if [ -f /etc/nginx/mime.types ]; then
  cp /etc/nginx/mime.types /opt/nginx/conf/mime.types
elif [ -f /opt/nginx/conf/mime.types ]; then
  :
else
  echo "mime.types missing after make install"; ls -la /etc/nginx /opt/nginx/conf || true; exit 1
fi
test -f /opt/nginx/conf/mime.types
nginx -V 2>&1 | tee /tmp/nginx-V.txt
grep -F "nginx/${NGINX_VERSION}" /tmp/nginx-V.txt
grep -F -- "-mtune=native" /tmp/nginx-V.txt
grep -F -- "-ljemalloc" /tmp/nginx-V.txt
grep -F -- "--with-zlib-opt=--zlib-compat" /tmp/nginx-V.txt || \
  grep -F -- '--with-zlib-opt="--zlib-compat"' /tmp/nginx-V.txt
grep -Fi openssl /tmp/nginx-V.txt
grep -Fi brotli /tmp/nginx-V.txt
grep -Fi zstd /tmp/nginx-V.txt
ldd /usr/sbin/nginx | tee /tmp/nginx-ldd.txt
grep -F /opt/jemalloc /tmp/nginx-ldd.txt
if grep -E '/usr/lib/libzstd|/lib/libzstd' /tmp/nginx-ldd.txt; then
  echo "apk libzstd leaked into nginx"; exit 1
fi
# llvm22-dev pulls zstd apk in proxy-build; runtime stage enforces no apk zstd.
grep -F /opt/jemalloc /tmp/nginx-ldd.txt
rm -rf /usr/src /tmp/nginx.tar.gz /tmp/nginx-V.txt /tmp/nginx-ldd.txt
if [ -e /usr/src ]; then echo "leftover /usr/src"; exit 1; fi
BASH

# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION}

ARG NGINX_VERSION=1.31.6

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
  grep -Fi zstd /tmp/nginx-V-runtime.txt; \
  ldd /usr/sbin/nginx | tee /tmp/nginx-ldd-runtime.txt; \
  grep -F /opt/jemalloc /tmp/nginx-ldd-runtime.txt; \
  if grep -E '/usr/lib/libzstd|/lib/libzstd' /tmp/nginx-ldd-runtime.txt; then \
    echo "apk libzstd leaked into runtime nginx"; exit 1; \
  fi; \
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
    rm -rf /build; \
    if apk info -e zstd >/dev/null 2>&1; then echo "apk zstd present"; exit 1; fi; \
    if apk info -e libzstd >/dev/null 2>&1; then echo "apk libzstd present"; exit 1; fi; \
    if apk info -e zstd-dev >/dev/null 2>&1; then echo "apk zstd-dev present"; exit 1; fi

STOPSIGNAL SIGTERM

CMD ["/usr/local/bin/proxy_entrypoint.sh"]
