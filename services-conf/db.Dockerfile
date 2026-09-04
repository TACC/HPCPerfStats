# Homegrown PostgreSQL 18 + TimescaleDB on pinned Alpine 3.24 musl.
# Build on the CPU that will run the image (-march=native).
# Context: ./services-conf (compose build.context).
# Do not use a floating Alpine tag. Do not copy docker-library --disable-rpath.

ARG ALPINE_VERSION=3.24.1

FROM alpine:${ALPINE_VERSION} AS db-build

ARG PG_VERSION=18.6
ARG PG_SHA256=555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f
ARG TIMESCALEDB_VERSION=2.29.2
ARG TIMESCALEDB_SHA256=3817f8acb8e167bf22b873a4c4e17d801089ed5a34c232eedd4f86dc222c8dc6
ARG JEMALLOC_VERSION=5.3.1
ARG JEMALLOC_SHA256=3826bc80232f22ed5c4662f3034f799ca316e819103bdc7bb99018a421706f92
ARG ICU_VERSION=78.3
ARG ICU_SHA256=3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0
ARG LIBURING_VERSION=2.15
ARG LIBURING_SHA256=8d052f2622dcb3678cbaee5ff582a87572672a6c0a56533cdda5b65cb636120a
ARG LZ4_VERSION=1.10.0
ARG LZ4_SHA256=537512904744b35e232912055ccf8ec66d768639ff3abe5788d90d792ec5f48b
ARG ZLIB_NG_VERSION=2.2.5
ARG ZLIB_NG_SHA256=5b3b022489f3ced82384f06db1e13ba148cbce38c7941e424d6cb414416acd18
ARG ZSTD_VERSION=1.5.7
ARG ZSTD_SHA256=eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3

# LLVM major matches docker-library postgres 18/alpine3.24.
ENV DOCKER_PG_LLVM_DEPS="llvm21-dev clang21"
ENV OPT_CFLAGS_LIBS="-O3 -march=native -flto=auto -g0"
ENV OPT_CFLAGS_PG="-O3 -march=native -mprefer-vector-width=512 -mtune=native -flto=auto -g0"

RUN set -eux; \
  apk add --no-cache \
    bash \
    bison \
    build-base \
    cmake \
    coreutils \
    curl \
    dpkg \
    dpkg-dev \
    flex \
    git \
    krb5-dev \
    libedit-dev \
    libxml2-dev \
    libxslt-dev \
    linux-headers \
    make \
    openssl-dev \
    perl \
    pkgconf \
    python3 \
    util-linux-dev \
    $DOCKER_PG_LLVM_DEPS
# libz ABI is /opt/zlib-ng (ZLIB_COMPAT); do not apk-install stock zlib packages.

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

# --- ICU (source under /opt/icu; not apk icu-dev as linked ABI) ---
RUN set -eux; \
  curl -fsSL "https://github.com/unicode-org/icu/releases/download/release-${ICU_VERSION}/icu4c-${ICU_VERSION}-sources.tgz" \
    -o /tmp/icu.tgz; \
  echo "${ICU_SHA256}  /tmp/icu.tgz" | sha256sum -c -; \
  mkdir -p /usr/src/icu; \
  tar -xzf /tmp/icu.tgz -C /usr/src/icu; \
  cd /usr/src/icu/icu/source; \
  CFLAGS="${OPT_CFLAGS_LIBS}" CXXFLAGS="${OPT_CFLAGS_LIBS}" \
    ./configure --prefix=/opt/icu --enable-static --disable-samples --disable-tests; \
  make -j"$(nproc)"; \
  make install; \
  rm -rf /usr/src/icu /tmp/icu.tgz

# --- liburing ---
RUN set -eux; \
  curl -fsSL "https://github.com/axboe/liburing/archive/refs/tags/liburing-${LIBURING_VERSION}.tar.gz" \
    -o /tmp/liburing.tar.gz; \
  echo "${LIBURING_SHA256}  /tmp/liburing.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/liburing; \
  tar -xzf /tmp/liburing.tar.gz -C /usr/src/liburing --strip-components=1; \
  cd /usr/src/liburing; \
  ./configure --prefix=/opt/liburing; \
  make -j"$(nproc)" CFLAGS="${OPT_CFLAGS_LIBS}"; \
  make install; \
  rm -rf /usr/src/liburing /tmp/liburing.tar.gz

# --- lz4 ---
RUN set -eux; \
  curl -fsSL "https://github.com/lz4/lz4/archive/refs/tags/v${LZ4_VERSION}.tar.gz" \
    -o /tmp/lz4.tar.gz; \
  echo "${LZ4_SHA256}  /tmp/lz4.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/lz4; \
  tar -xzf /tmp/lz4.tar.gz -C /usr/src/lz4 --strip-components=1; \
  cd /usr/src/lz4; \
  make -j"$(nproc)" CFLAGS="${OPT_CFLAGS_LIBS}" PREFIX=/opt/lz4; \
  make install PREFIX=/opt/lz4; \
  rm -rf /usr/src/lz4 /tmp/lz4.tar.gz

# --- zlib-ng (ZLIB_COMPAT → libz.so; match Python image /opt/zlib-ng pin) ---
RUN set -eux; \
  curl -fsSL "https://github.com/zlib-ng/zlib-ng/archive/refs/tags/${ZLIB_NG_VERSION}.tar.gz" \
    -o /tmp/zlib-ng.tar.gz; \
  echo "${ZLIB_NG_SHA256}  /tmp/zlib-ng.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/zlib-ng; \
  tar -xzf /tmp/zlib-ng.tar.gz -C /usr/src/zlib-ng --strip-components=1; \
  cd /usr/src/zlib-ng; \
  cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/zlib-ng \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DZLIB_COMPAT=ON \
    -DZLIB_ENABLE_TESTS=OFF \
    -DZLIBNG_ENABLE_TESTS=OFF \
    -DWITH_GTEST=OFF \
    -DWITH_OPTIM=ON \
    -DWITH_NEW_STRATEGIES=ON \
    -DWITH_NATIVE_INSTRUCTIONS=ON \
    -DCMAKE_C_FLAGS="${OPT_CFLAGS_LIBS}"; \
  cmake --build build -j"$(nproc)"; \
  cmake --install build; \
  test -f /opt/zlib-ng/lib/libz.so || test -f /opt/zlib-ng/lib/libz.so.1; \
  rm -rf /usr/src/zlib-ng /tmp/zlib-ng.tar.gz

# --- zstd (match Python image /opt/zstd 1.5.7 pin; gzip→zlib-ng, .lz4→/opt/lz4) ---
RUN set -eux; \
  curl -fsSL "https://github.com/facebook/zstd/releases/download/v${ZSTD_VERSION}/zstd-${ZSTD_VERSION}.tar.gz" \
    -o /tmp/zstd.tar.gz; \
  echo "${ZSTD_SHA256}  /tmp/zstd.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/zstd; \
  tar -xzf /tmp/zstd.tar.gz -C /usr/src/zstd --strip-components=1; \
  cd /usr/src/zstd; \
  export PKG_CONFIG_PATH="/opt/lz4/lib/pkgconfig:/opt/zlib-ng/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"; \
  export CPPFLAGS="-I/opt/lz4/include -I/opt/zlib-ng/include${CPPFLAGS:+ $CPPFLAGS}"; \
  export LDFLAGS="-L/opt/lz4/lib -Wl,-rpath,/opt/lz4/lib -L/opt/zlib-ng/lib -Wl,-rpath,/opt/zlib-ng/lib${LDFLAGS:+ $LDFLAGS}"; \
  make -j"$(nproc)" PREFIX=/opt/zstd HAVE_ZLIB=1 HAVE_LZ4=1 MOREFLAGS="${OPT_CFLAGS_LIBS}"; \
  make install PREFIX=/opt/zstd; \
  ldd /opt/zstd/bin/zstd | tee /tmp/zstd.ldd; \
  grep -E '/opt/zlib-ng/.+libz' /tmp/zstd.ldd; \
  grep -E '/opt/lz4/.+liblz4' /tmp/zstd.ldd; \
  ! grep -E '[[:space:]](/lib/libz\.|/usr/lib/libz\.|/usr/lib/liblz4)' /tmp/zstd.ldd; \
  rm -rf /usr/src/zstd /tmp/zstd.tar.gz

ENV PKG_CONFIG_PATH="/opt/zlib-ng/lib/pkgconfig:/opt/icu/lib/pkgconfig:/opt/liburing/lib/pkgconfig:/opt/lz4/lib/pkgconfig:/opt/zstd/lib/pkgconfig" \
  PATH="/opt/jemalloc/bin:/opt/zstd/bin:/usr/local/bin:${PATH}" \
  LDFLAGS="-L/opt/jemalloc/lib -L/opt/zlib-ng/lib -L/opt/icu/lib -L/opt/liburing/lib -L/opt/lz4/lib -L/opt/zstd/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,-rpath,/opt/zlib-ng/lib -Wl,-rpath,/opt/icu/lib -Wl,-rpath,/opt/liburing/lib -Wl,-rpath,/opt/lz4/lib -Wl,-rpath,/opt/zstd/lib -Wl,--no-as-needed -ljemalloc" \
  CPPFLAGS="-I/opt/jemalloc/include -I/opt/zlib-ng/include -I/opt/icu/include -I/opt/liburing/include -I/opt/lz4/include -I/opt/zstd/include" \
  CFLAGS="${OPT_CFLAGS_PG}" \
  CXXFLAGS="${OPT_CFLAGS_PG}"

# --- PostgreSQL 18 ---
RUN set -eux; \
  curl -fsSL "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.bz2" \
    -o /tmp/postgresql.tar.bz2; \
  echo "${PG_SHA256}  /tmp/postgresql.tar.bz2" | sha256sum -c -; \
  mkdir -p /usr/src/postgresql; \
  tar -xjf /tmp/postgresql.tar.bz2 -C /usr/src/postgresql --strip-components=1; \
  rm -f /tmp/postgresql.tar.bz2; \
  cd /usr/src/postgresql; \
  awk '$1 == "#define" && $2 == "DEFAULT_PGSOCKET_DIR" && $3 == "\"/tmp\"" { $3 = "\"/var/run/postgresql\""; print; next } { print }' \
    src/include/pg_config_manual.h > src/include/pg_config_manual.h.new; \
  grep '/var/run/postgresql' src/include/pg_config_manual.h.new; \
  mv src/include/pg_config_manual.h.new src/include/pg_config_manual.h; \
  export LLVM_CONFIG="/usr/lib/llvm21/bin/llvm-config"; \
  export CLANG=clang-21; \
  gnuArch="$(dpkg-architecture --query DEB_BUILD_GNU_TYPE)"; \
  # Intentionally omit docker-library --disable-rpath so /opt rpaths stick.
  ./configure \
    --enable-option-checking=fatal \
    --build="$gnuArch" \
    --enable-integer-datetimes \
    --enable-thread-safety \
    --with-uuid=e2fs \
    --with-pgport=5432 \
    --with-system-tzdata=/usr/share/zoneinfo \
    --prefix=/usr/local \
    --with-includes=/usr/local/include \
    --with-libraries=/usr/local/lib \
    --with-icu \
    --with-liburing \
    --with-libxml \
    --with-libxslt \
    --with-llvm \
    --with-lz4 \
    --with-openssl \
    --with-zstd \
  ; \
  test ! -f config.log || ! grep -q 'disable-rpath' config.status || true; \
  ! grep -q -- '--disable-rpath' config.status; \
  make -j"$(nproc)" world-bin; \
  make install-world-bin; \
  make -C contrib install; \
  # Fail-closed: postgres DT_NEEDED must resolve /opt libs (not apk under /usr/lib).
  ldd /usr/local/bin/postgres | tee /tmp/postgres.ldd; \
  grep -E '/opt/jemalloc/.+libjemalloc' /tmp/postgres.ldd; \
  grep -E '/opt/zlib-ng/.+libz' /tmp/postgres.ldd; \
  grep -E '/opt/icu/.+libicu' /tmp/postgres.ldd; \
  grep -E '/opt/liburing/.+liburing' /tmp/postgres.ldd; \
  grep -E '/opt/lz4/.+liblz4' /tmp/postgres.ldd; \
  grep -E '/opt/zstd/.+libzstd' /tmp/postgres.ldd; \
  ! grep -E '[[:space:]](/lib/libz\.|/usr/lib/libz\.|/usr/lib/liblz4|/usr/lib/libzstd|/usr/lib/libicu|/usr/lib/liburing)' /tmp/postgres.ldd; \
  strip --strip-unneeded /usr/local/bin/postgres /usr/local/bin/psql || true; \
  postgres --version

# --- TimescaleDB (not APACHE_ONLY; share /opt lz4+zstd) ---
RUN set -eux; \
  curl -fsSL "https://github.com/timescale/timescaledb/archive/refs/tags/${TIMESCALEDB_VERSION}.tar.gz" \
    -o /tmp/timescaledb.tar.gz; \
  echo "${TIMESCALEDB_SHA256}  /tmp/timescaledb.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/timescaledb; \
  tar -xzf /tmp/timescaledb.tar.gz -C /usr/src/timescaledb --strip-components=1; \
  rm -f /tmp/timescaledb.tar.gz; \
  cd /usr/src/timescaledb; \
  export PATH="/usr/local/bin:${PATH}"; \
  export PKG_CONFIG_PATH="/opt/lz4/lib/pkgconfig:/opt/zstd/lib/pkgconfig:${PKG_CONFIG_PATH}"; \
  ./bootstrap \
    -DCMAKE_BUILD_TYPE=Release \
    -DREGRESS_CHECKS=OFF \
    -DTAP_CHECKS=OFF \
    -DCMAKE_PREFIX_PATH="/opt/lz4;/opt/zstd" \
    -DCMAKE_INSTALL_RPATH="/opt/lz4/lib;/opt/zstd/lib" \
    -DCMAKE_BUILD_RPATH="/opt/lz4/lib;/opt/zstd/lib" \
    -DCMAKE_C_FLAGS="${OPT_CFLAGS_PG}" \
  ; \
  ! grep -R APACHE_ONLY build 2>/dev/null | grep -q 'ON' || true; \
  cmake -L build | tee /tmp/ts.cmake; \
  ! grep -qi 'APACHE_ONLY:BOOL=ON' /tmp/ts.cmake; \
  cd build; \
  make -j"$(nproc)"; \
  make install; \
  TS_SO="$(find /usr/local/lib/postgresql -name 'timescaledb.so' | head -1)"; \
  test -n "$TS_SO"; \
  ldd "$TS_SO" | tee /tmp/timescaledb.ldd; \
  grep -E '/opt/lz4/.+liblz4' /tmp/timescaledb.ldd; \
  grep -E '/opt/zstd/.+libzstd' /tmp/timescaledb.ldd; \
  ! grep -E '[[:space:]](/usr/lib/liblz4|/usr/lib/libzstd)' /tmp/timescaledb.ldd; \
  sed -ri "s/#?(shared_preload_libraries)\s*=.*/\1 = 'timescaledb'/" \
    /usr/local/share/postgresql/postgresql.conf.sample; \
  grep -F "shared_preload_libraries = 'timescaledb'" \
    /usr/local/share/postgresql/postgresql.conf.sample; \
  rm -rf /usr/src/timescaledb /usr/src/postgresql

# Prune docs/man from the install tree copied to runtime.
RUN set -eux; \
  rm -rf /usr/local/share/doc /usr/local/share/man; \
  test ! -d /usr/src || rm -rf /usr/src; \
  test ! -d /usr/src

# ---------- runtime ----------
FROM alpine:${ALPINE_VERSION}

# 70 is the standard uid/gid for "postgres" in Alpine
RUN set -eux; \
  addgroup -g 70 -S postgres; \
  adduser -u 70 -S -D -G postgres -H -h /var/lib/postgresql -s /bin/sh postgres; \
  install --verbose --directory --owner postgres --group postgres --mode 1777 /var/lib/postgresql

ENV GOSU_VERSION=1.19
RUN set -eux; \
  apk add --no-cache --virtual .gosu-deps ca-certificates dpkg gnupg; \
  dpkgArch="$(dpkg --print-architecture | awk -F- '{ print $NF }')"; \
  wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch"; \
  wget -O /usr/local/bin/gosu.asc "https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch.asc"; \
  export GNUPGHOME="$(mktemp -d)"; \
  gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys B42F6819007F00F88E364FD4036A9C25BF357DD4; \
  gpg --batch --verify /usr/local/bin/gosu.asc /usr/local/bin/gosu; \
  gpgconf --kill all; \
  rm -rf "$GNUPGHOME" /usr/local/bin/gosu.asc; \
  apk del --no-network .gosu-deps; \
  chmod +x /usr/local/bin/gosu; \
  gosu --version; \
  gosu nobody true

ENV LANG=en_US.utf8
RUN mkdir /docker-entrypoint-initdb.d

COPY --from=db-build /opt/jemalloc /opt/jemalloc
COPY --from=db-build /opt/zlib-ng /opt/zlib-ng
COPY --from=db-build /opt/icu /opt/icu
COPY --from=db-build /opt/liburing /opt/liburing
COPY --from=db-build /opt/lz4 /opt/lz4
COPY --from=db-build /opt/zstd /opt/zstd
COPY --from=db-build /usr/local /usr/local

RUN set -eux; \
  apk add --no-cache \
    bash \
    libedit \
    libxml2 \
    libxslt \
    openssl \
    tzdata \
    # ICU data files for collations (source ICU may still need full locale data).
    # zlib/libz: /opt/zlib-ng only — do not apk-add zlib.
    icu-data-full \
  ; \
  # Transitive shared objects that scanelf would normally pull from apk for
  # non-/opt deps (libssl, libcrypto already via openssl).
  runDeps="$( \
    scanelf --needed --nobanner --format '%n#p' --recursive /usr/local /opt/zstd \
      | tr ',' '\n' \
      | sort -u \
      | awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/jemalloc/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/zlib-ng/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/icu/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/liburing/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/lz4/lib/" $1 " ]") == 0 { next } \
             system("[ -e /opt/zstd/lib/" $1 " ]") == 0 { next } \
             { print "so:" $1 }' \
  )"; \
  # Fail closed if scanelf still wants apk zlib/libz.
  ! printf '%s\n' "$runDeps" | grep -E '^so:libz(\.so|$)'; \
  apk add --no-cache $runDeps; \
  ! apk info -e zlib >/dev/null 2>&1; \
  install --verbose --directory --owner postgres --group postgres --mode 3777 /var/run/postgresql; \
  # Fail-closed again on the slim runtime tree.
  ldd /usr/local/bin/postgres | tee /tmp/postgres.ldd; \
  grep -E '/opt/jemalloc/.+libjemalloc' /tmp/postgres.ldd; \
  grep -E '/opt/zlib-ng/.+libz' /tmp/postgres.ldd; \
  grep -E '/opt/icu/.+libicu' /tmp/postgres.ldd; \
  grep -E '/opt/liburing/.+liburing' /tmp/postgres.ldd; \
  grep -E '/opt/lz4/.+liblz4' /tmp/postgres.ldd; \
  grep -E '/opt/zstd/.+libzstd' /tmp/postgres.ldd; \
  ! grep -E '[[:space:]](/lib/libz\.|/usr/lib/libz\.)' /tmp/postgres.ldd; \
  ldd /opt/zstd/bin/zstd | tee /tmp/zstd.ldd; \
  grep -E '/opt/zlib-ng/.+libz' /tmp/zstd.ldd; \
  grep -E '/opt/lz4/.+liblz4' /tmp/zstd.ldd; \
  ! grep -E '[[:space:]](/lib/libz\.|/usr/lib/libz\.|/usr/lib/liblz4)' /tmp/zstd.ldd; \
  sed -ri "s!^#?(listen_addresses)\s*=\s*\S+.*!\1 = '*'!" \
    /usr/local/share/postgresql/postgresql.conf.sample; \
  grep -F "listen_addresses = '*'" /usr/local/share/postgresql/postgresql.conf.sample; \
  grep -F "shared_preload_libraries = 'timescaledb'" \
    /usr/local/share/postgresql/postgresql.conf.sample; \
  postgres --version

ENV PG_MAJOR=18 \
  PG_VERSION=18.6 \
  PGDATA=/var/lib/postgresql/18/docker \
  TIMESCALEDB_TELEMETRY=off \
  LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2 \
  PATH=/opt/zstd/bin:/usr/local/bin:/usr/bin:/bin

VOLUME /var/lib/postgresql

COPY db-docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY db-docker-ensure-initdb.sh /usr/local/bin/docker-ensure-initdb.sh
RUN set -eux; \
  chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/docker-ensure-initdb.sh; \
  ln -sT docker-ensure-initdb.sh /usr/local/bin/docker-enforce-initdb.sh

ENTRYPOINT ["docker-entrypoint.sh"]
STOPSIGNAL SIGINT
EXPOSE 5432
CMD ["postgres"]
