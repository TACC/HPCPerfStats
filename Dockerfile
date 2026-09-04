# Build frontend assets in a dedicated node stage.
# COPY is scoped to frontend inputs so Python/backend changes do not bust npm layers.
FROM node:26.5.1-alpine3.23 AS frontend-builder
# Pin npm 12+ before package install: dependency lifecycle scripts are opt-in
# (allowScripts) so wormed preinstall hooks cannot run by default.
ARG NPM_VERSION=12.0.2
RUN apk add --no-cache bash git \
    && npm install -g "npm@${NPM_VERSION}" \
    && npm --version
ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend

# Dependencies: cached until package-lock.json changes.
COPY --chown=node:node hpcperfstats/site/frontend/package.json \
    hpcperfstats/site/frontend/package-lock.json \
    ./
# Vite and @vitejs/plugin-react are devDependencies used by Vitest; they must be present to build.
RUN /bin/bash -o pipefail -c "npm ci"

# OpenAPI (Orval) + site identity ini: cached until spec or ini template changes.
WORKDIR /home/hpcperfstats
COPY --chown=node:node hpcperfstats/site/openapi/openapi.yaml \
    hpcperfstats/site/openapi/
COPY --chown=node:node hpcperfstats.ini ./

WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend

# Frontend source: cached until site/frontend changes.
COPY --chown=node:node hpcperfstats/site/frontend/ ./

# Context .git for SPA commit bake (checkout root = /home/hpcperfstats).
# Optional ARG override; do not ENV=unknown before build — that leaves the SPA
# with an empty SITE_GIT_COMMIT when git rev-parse also fails silently.
COPY .git /home/hpcperfstats/.git
ARG HPCPERFSTATS_GIT_COMMIT=unknown
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  git config --global --add safe.directory /home/hpcperfstats; \
  COMMIT="${HPCPERFSTATS_GIT_COMMIT:-unknown}"; \
  if [[ -z "${COMMIT}" || "${COMMIT}" == "unknown" ]]; then \
    COMMIT="$(git -C /home/hpcperfstats rev-parse HEAD)"; \
  fi; \
  export HPCPERFSTATS_GIT_COMMIT="${COMMIT}"; \
  echo "frontend-builder: HPCPERFSTATS_GIT_COMMIT=${HPCPERFSTATS_GIT_COMMIT}"; \
  npm run build:prod'

WORKDIR /home/hpcperfstats
RUN /bin/bash -o pipefail -c "\
    mkdir -p /tmp/frontend-static && \
    cp -a hpcperfstats/site/hpcperfstats_site/static/frontend/. /tmp/frontend-static/"

# Build both CPython 3.14.7 ABIs + jemalloc + native libmpdec/libffi on Debian trixie.
# -march=native: build on the prod host that runs the image (same as MKL stack).
# GIL: --without-mimalloc so jemalloc owns process + object-arena malloc.
# FT: keep mimalloc for objects; still force-link jemalloc for side allocation.
# Jemalloc both ways: DT_NEEDED here + runtime LD_PRELOAD + /etc/ld.so.preload for wheels.
FROM debian:trixie AS python-build
ENV PYTHON_VERSION=3.14.7 \
    MAKEFLAGS=-j40 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_WARN_SCRIPT_LOCATION=false \
    PIP_ROOT_USER_ACTION=ignore \
    PKG_CONFIG_PATH=/opt/zstd/lib/pkgconfig:/opt/mpdecimal/lib/pkgconfig:/opt/libffi/lib/pkgconfig:/opt/libffi/lib/x86_64-linux-gnu/pkgconfig

# Builder apt toolchain (compilers stay in python-build only).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  test "$(uname -m)" = "x86_64"; \
  apt-get update -y; \
  apt-get install -y --no-install-recommends \
    build-essential gfortran ninja-build cmake pkg-config \
    curl ca-certificates autoconf \
    libssl-dev libncursesw5-dev libsqlite3-dev \
    libreadline-dev libbz2-dev liblzma-dev tk-dev uuid-dev \
    libgdbm-dev libnss3-dev libexpat1-dev \
    default-libmysqlclient-dev file binutils; \
  apt-get clean; \
  rm -rf /var/lib/apt/lists/*'

# jemalloc 5.3.1 (shared; keep default initial-exec TLS — do not disable it).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  PAGE_SIZE="$(getconf PAGE_SIZE)"; \
  case "${PAGE_SIZE}" in \
    (*[!0-9]*|"") echo "bad PAGE_SIZE=${PAGE_SIZE}"; false ;; \
  esac; \
  LG_PAGE=0; N="${PAGE_SIZE}"; \
  while [ "$((N % 2))" -eq 0 ] && [ "${N}" -gt 1 ]; do N=$((N / 2)); LG_PAGE=$((LG_PAGE + 1)); done; \
  test "${N}" -eq 1; \
  test "$((1 << LG_PAGE))" -eq "${PAGE_SIZE}"; \
  echo "jemalloc --with-lg-page=${LG_PAGE} (PAGE_SIZE=${PAGE_SIZE})"; \
  curl -fsSL "https://github.com/jemalloc/jemalloc/releases/download/5.3.1/jemalloc-5.3.1.tar.bz2" \
    -o /tmp/jemalloc.tar.bz2; \
  echo "3826bc80232f22ed5c4662f3034f799ca316e819103bdc7bb99018a421706f92  /tmp/jemalloc.tar.bz2" | sha256sum -c -; \
  mkdir -p /usr/src/jemalloc; \
  tar -xjf /tmp/jemalloc.tar.bz2 -C /usr/src/jemalloc --strip-components=1; \
  rm -f /tmp/jemalloc.tar.bz2; \
  cd /usr/src/jemalloc; \
  export CFLAGS="-O3 -march=native -flto -g0" CXXFLAGS="-O3 -march=native -flto -g0"; \
  ./configure --prefix=/opt/jemalloc --enable-shared --disable-static \
    --disable-stats --disable-fill --disable-debug --with-lg-page="${LG_PAGE}"; \
  make -j40; make install; \
  find /opt/jemalloc -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  echo "/opt/jemalloc/lib" > /etc/ld.so.conf.d/jemalloc.conf; \
  ldconfig; \
  rm -rf /usr/src/jemalloc'

# zlib-ng 2.2.5 (ZLIB_COMPAT → libz.so.1). CPython and source builds link this; no apt zlib*.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://github.com/zlib-ng/zlib-ng/archive/refs/tags/2.2.5.tar.gz" \
    -o /tmp/zlib-ng.tar.gz; \
  echo "5b3b022489f3ced82384f06db1e13ba148cbce38c7941e424d6cb414416acd18  /tmp/zlib-ng.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/zlib-ng; \
  tar -xzf /tmp/zlib-ng.tar.gz -C /usr/src/zlib-ng --strip-components=1; \
  rm -f /tmp/zlib-ng.tar.gz; \
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
    -DCMAKE_C_FLAGS="-O3 -march=native -flto -g0"; \
  cmake --build build -j40; \
  cmake --install build; \
  find /opt/zlib-ng -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  test -f /opt/zlib-ng/lib/libz.so || test -f /opt/zlib-ng/lib/libz.so.1; \
  echo "/opt/zlib-ng/lib" > /etc/ld.so.conf.d/zlib-ng.conf; \
  ldconfig; \
  rm -rf /usr/src/zlib-ng'

# zstd 1.5.7 (lib + CLI). Link gzip/zlib support to /opt/zlib-ng; no apt zstd.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz" \
    -o /tmp/zstd.tar.gz; \
  echo "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3  /tmp/zstd.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/zstd; \
  tar -xzf /tmp/zstd.tar.gz -C /usr/src/zstd --strip-components=1; \
  rm -f /tmp/zstd.tar.gz; \
  cd /usr/src/zstd; \
  export PKG_CONFIG_PATH="/opt/zlib-ng/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"; \
  export CPPFLAGS="-I/opt/zlib-ng/include${CPPFLAGS:+ $CPPFLAGS}"; \
  export LDFLAGS="-L/opt/zlib-ng/lib -Wl,-rpath,/opt/zlib-ng/lib${LDFLAGS:+ $LDFLAGS}"; \
  export MOREFLAGS="-O3 -march=native -flto -g0"; \
  make -j40 PREFIX=/opt/zstd HAVE_ZLIB=1; \
  make install PREFIX=/opt/zstd; \
  find /opt/zstd -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  test -x /opt/zstd/bin/zstd; \
  test -f /opt/zstd/lib/libzstd.so || test -f /opt/zstd/lib/libzstd.so.1; \
  ldd /opt/zstd/bin/zstd | grep '/opt/zlib-ng/.*libz'; \
  echo "/opt/zstd/lib" > /etc/ld.so.conf.d/zstd.conf; \
  ldconfig; \
  /opt/zstd/bin/zstd --version; \
  rm -rf /usr/src/zstd'

# mpdecimal 4.0.1 (x86_64 MACHINE=x64 → CONFIG_64 + ASM).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-4.0.1.tar.gz" \
    -o /tmp/mpdecimal.tar.gz; \
  echo "96d33abb4bb0070c7be0fed4246cd38416188325f820468214471938545b1ac8  /tmp/mpdecimal.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/mpdecimal; \
  tar -xzf /tmp/mpdecimal.tar.gz -C /usr/src/mpdecimal --strip-components=1; \
  rm -f /tmp/mpdecimal.tar.gz; \
  cd /usr/src/mpdecimal; \
  export CFLAGS="-O3 -march=native -DCONFIG_64 -DASM -flto -g0" \
    CXXFLAGS="-O3 -march=native -DCONFIG_64 -DASM -flto -g0"; \
  ./configure --prefix=/opt/mpdecimal MACHINE=x64; \
  make -j40; make install; \
  find /opt/mpdecimal -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  echo "/opt/mpdecimal/lib" > /etc/ld.so.conf.d/mpdecimal.conf; \
  ldconfig; \
  rm -rf /usr/src/mpdecimal'

# libffi 3.8.0 (--with-gcc-arch=native).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://github.com/libffi/libffi/releases/download/v3.8.0/libffi-3.8.0.tar.gz" \
    -o /tmp/libffi.tar.gz; \
  echo "7da3e2d9a171eb0a038f592ecad3ff2bb2550f3496d87b3b29ad0cf4430c0db4  /tmp/libffi.tar.gz" | sha256sum -c -; \
  mkdir -p /usr/src/libffi; \
  tar -xzf /tmp/libffi.tar.gz -C /usr/src/libffi --strip-components=1; \
  rm -f /tmp/libffi.tar.gz; \
  cd /usr/src/libffi; \
  export CFLAGS="-O3 -march=native -flto -g0" CXXFLAGS="-O3 -march=native -flto -g0"; \
  ./configure --prefix=/opt/libffi --with-gcc-arch=native --disable-static --enable-shared; \
  make -j40; make install; \
  find /opt/libffi -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  if [ -d /opt/libffi/lib/x86_64-linux-gnu ]; then \
    echo "/opt/libffi/lib/x86_64-linux-gnu" > /etc/ld.so.conf.d/libffi.conf; \
  else \
    echo "/opt/libffi/lib" > /etc/ld.so.conf.d/libffi.conf; \
  fi; \
  ldconfig; \
  rm -rf /usr/src/libffi'

# GIL CPython 3.14.7 (--without-mimalloc; force-link jemalloc).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" \
    -o /tmp/Python.tgz; \
  echo "62859805f6fdf25e2bcbf3fa3217801e1996887ca33e6a2af80674bdfa2dbe07  /tmp/Python.tgz" | sha256sum -c -; \
  mkdir -p /usr/src/python; \
  tar -xzf /tmp/Python.tgz -C /usr/src/python --strip-components=1; \
  rm -f /tmp/Python.tgz; \
  cd /usr/src/python; \
  export PKG_CONFIG_PATH="/opt/zstd/lib/pkgconfig:/opt/zlib-ng/lib/pkgconfig:/opt/mpdecimal/lib/pkgconfig:/opt/libffi/lib/pkgconfig:/opt/libffi/lib/x86_64-linux-gnu/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"; \
  export CPPFLAGS="-I/opt/zstd/include -I/opt/zlib-ng/include${CPPFLAGS:+ $CPPFLAGS}"; \
  export CFLAGS="-O3 -march=native -g0" CXXFLAGS="-O3 -march=native -g0" OPT="-O3 -g0"; \
  LIBFFI_LIBDIR="/opt/libffi/lib"; \
  if [ -d /opt/libffi/lib/x86_64-linux-gnu ]; then LIBFFI_LIBDIR="/opt/libffi/lib/x86_64-linux-gnu"; fi; \
  export LIBFFI_CFLAGS="-I/opt/libffi/include" LIBFFI_LIBS="-L${LIBFFI_LIBDIR} -lffi"; \
  export LDFLAGS="-L/opt/zstd/lib -L/opt/zlib-ng/lib -L/opt/jemalloc/lib -L/opt/mpdecimal/lib -L${LIBFFI_LIBDIR} -Wl,-rpath,/opt/zstd/lib -Wl,-rpath,/opt/zlib-ng/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,-rpath,/opt/mpdecimal/lib -Wl,-rpath,${LIBFFI_LIBDIR} -Wl,-rpath,/opt/python3.14/lib -Wl,--no-as-needed -ljemalloc -Wl,--as-needed"; \
  ./configure \
    --prefix=/opt/python3.14 \
    --enable-shared \
    --enable-optimizations \
    --with-lto \
    --with-ensurepip=install \
    --with-system-libmpdec \
    --without-static-libpython \
    --disable-test-modules \
    --without-mimalloc; \
  make -j40; \
  make install; \
  /opt/python3.14/bin/python3.14 -c "import sysconfig; assert int(sysconfig.get_config_var(\"Py_GIL_DISABLED\") or 0) == 0"; \
  ldd /opt/python3.14/bin/python3.14 | grep libjemalloc; \
  ldd /opt/python3.14/lib/libpython3.14.so | grep libjemalloc; \
  zlib_so="$(find /opt/python3.14 -name 'zlib*.so' -type f | head -1)"; \
  test -n "$zlib_so"; \
  ldd "$zlib_so" | grep '/opt/zlib-ng/.*libz'; \
  decimal_so="$(find /opt/python3.14 -name '_decimal*.so' -type f | head -1)"; \
  test -n "$decimal_so"; \
  ldd "$decimal_so" | grep libmpdec; \
  ctypes_so="$(find /opt/python3.14 -name '_ctypes*.so' -type f | head -1)"; \
  test -n "$ctypes_so"; \
  ldd "$ctypes_so" | grep '/opt/libffi/.*libffi'; \
  zstd_so="$(find /opt/python3.14 -name '_zstd*.so' -type f | head -1)"; \
  test -n "$zstd_so"; \
  ldd "$zstd_so" | grep '/opt/zstd/.*libzstd'; \
  /opt/python3.14/bin/python3.14 -c "import zlib; v=getattr(zlib,\"ZLIB_RUNTIME_VERSION\",zlib.ZLIB_VERSION); assert \"zlib-ng\" in str(v).lower(), v"; \
  /opt/python3.14/bin/python3.14 -c "import _zstd, compression.zstd as z; assert _zstd.zstd_version.startswith(\"1.5.\"), _zstd.zstd_version"; \
  find /opt/python3.14 -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  mkdir -p /usr/local/bin /usr/local/lib; \
  ln -sfn /opt/python3.14/bin/python3 /usr/local/bin/python3; \
  ln -sfn /opt/python3.14/bin/python3.14 /usr/local/bin/python3.14; \
  ln -sfn /opt/python3.14/bin/pip3 /usr/local/bin/pip3; \
  ln -sfn /opt/python3.14/bin/pip3.14 /usr/local/bin/pip3.14; \
  for so in /opt/python3.14/lib/libpython3.14.so*; do ln -sfn "$so" "/usr/local/lib/$(basename "$so")"; done; \
  echo "/opt/python3.14/lib" > /etc/ld.so.conf.d/python314.conf; \
  ldconfig; \
  for bin in /opt/python3.14/bin/python3.14 /opt/python3.14/lib/libpython3.14.so /opt/jemalloc/lib/libjemalloc.so.2; do \
    file -b "$bin" | grep -qi "not stripped" && { echo "unstripped $bin"; false; }; \
    if readelf -S "$bin" | grep -q "\.debug_info"; then echo "debug_info $bin"; false; fi; \
  done; \
  rm -rf /usr/src/python'

# Free-threaded CPython 3.14.7 (mimalloc required; still force-link jemalloc).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  curl -fsSL "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" \
    -o /tmp/Python.tgz; \
  echo "62859805f6fdf25e2bcbf3fa3217801e1996887ca33e6a2af80674bdfa2dbe07  /tmp/Python.tgz" | sha256sum -c -; \
  mkdir -p /usr/src/python; \
  tar -xzf /tmp/Python.tgz -C /usr/src/python --strip-components=1; \
  rm -f /tmp/Python.tgz; \
  cd /usr/src/python; \
  export PKG_CONFIG_PATH="/opt/zstd/lib/pkgconfig:/opt/zlib-ng/lib/pkgconfig:/opt/mpdecimal/lib/pkgconfig:/opt/libffi/lib/pkgconfig:/opt/libffi/lib/x86_64-linux-gnu/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"; \
  export CPPFLAGS="-I/opt/zstd/include -I/opt/zlib-ng/include${CPPFLAGS:+ $CPPFLAGS}"; \
  export CFLAGS="-O3 -march=native -g0" CXXFLAGS="-O3 -march=native -g0" OPT="-O3 -g0"; \
  LIBFFI_LIBDIR="/opt/libffi/lib"; \
  if [ -d /opt/libffi/lib/x86_64-linux-gnu ]; then LIBFFI_LIBDIR="/opt/libffi/lib/x86_64-linux-gnu"; fi; \
  export LIBFFI_CFLAGS="-I/opt/libffi/include" LIBFFI_LIBS="-L${LIBFFI_LIBDIR} -lffi"; \
  export LDFLAGS="-L/opt/zstd/lib -L/opt/zlib-ng/lib -L/opt/jemalloc/lib -L/opt/mpdecimal/lib -L${LIBFFI_LIBDIR} -Wl,-rpath,/opt/zstd/lib -Wl,-rpath,/opt/zlib-ng/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,-rpath,/opt/mpdecimal/lib -Wl,-rpath,${LIBFFI_LIBDIR} -Wl,-rpath,/opt/python3.14t/lib -Wl,--no-as-needed -ljemalloc -Wl,--as-needed"; \
  ./configure \
    --prefix=/opt/python3.14t \
    --enable-shared \
    --enable-optimizations \
    --with-lto \
    --with-ensurepip=install \
    --with-system-libmpdec \
    --without-static-libpython \
    --disable-test-modules \
    --disable-gil; \
  make -j40; \
  make install; \
  ln -sf python3.14t /opt/python3.14t/bin/python; \
  ln -sf python3.14t /opt/python3.14t/bin/python3; \
  /opt/python3.14t/bin/python3.14t -c "import sysconfig; assert int(sysconfig.get_config_var(\"Py_GIL_DISABLED\") or 0) == 1"; \
  ldd /opt/python3.14t/bin/python3.14t | grep libjemalloc; \
  (ldd /opt/python3.14t/lib/libpython3.14t.so 2>/dev/null || ldd /opt/python3.14t/lib/libpython3.14.so) | grep libjemalloc; \
  zlib_so="$(find /opt/python3.14t -name 'zlib*.so' -type f | head -1)"; \
  test -n "$zlib_so"; \
  ldd "$zlib_so" | grep '/opt/zlib-ng/.*libz'; \
  decimal_so="$(find /opt/python3.14t -name '_decimal*.so' -type f | head -1)"; \
  test -n "$decimal_so"; \
  ldd "$decimal_so" | grep libmpdec; \
  ctypes_so="$(find /opt/python3.14t -name '_ctypes*.so' -type f | head -1)"; \
  test -n "$ctypes_so"; \
  ldd "$ctypes_so" | grep '/opt/libffi/.*libffi'; \
  zstd_so="$(find /opt/python3.14t -name '_zstd*.so' -type f | head -1)"; \
  test -n "$zstd_so"; \
  ldd "$zstd_so" | grep '/opt/zstd/.*libzstd'; \
  /opt/python3.14t/bin/python3.14t -c "import zlib; v=getattr(zlib,\"ZLIB_RUNTIME_VERSION\",zlib.ZLIB_VERSION); assert \"zlib-ng\" in str(v).lower(), v"; \
  /opt/python3.14t/bin/python3.14t -c "import _zstd, compression.zstd as z; assert _zstd.zstd_version.startswith(\"1.5.\"), _zstd.zstd_version"; \
  find /opt/python3.14t -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  echo "/opt/python3.14t/lib" > /etc/ld.so.conf.d/python314t.conf; \
  ldconfig; \
  for bin in /opt/python3.14t/bin/python3.14t /opt/jemalloc/lib/libjemalloc.so.2 /opt/mpdecimal/lib/libmpdec.so /opt/libffi/lib/libffi.so; do \
    [ -e "$bin" ] || bin="$(ls ${bin}* 2>/dev/null | head -1)"; \
    file -b "$bin" | grep -qi "not stripped" && { echo "unstripped $bin"; false; }; \
    if readelf -S "$bin" 2>/dev/null | grep -q "\.debug_info"; then echo "debug_info $bin"; false; fi; \
  done; \
  rm -rf /usr/src/python'

# Prefix bins must be on PATH so image-build console scripts (cython, meson,
# ninja) are visible to Meson under --no-build-isolation. Symlinking only
# python3/pip3 into /usr/local/bin is not enough. Per-ABI pip/MKL RUNs still
# prepend that ABI's /opt/.../bin so Meson does not mix GIL and FT cython.
ENV PATH=/usr/local/bin:/opt/zstd/bin:/opt/python3.14/bin:/opt/python3.14t/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /home/hpcperfstats

# Dual-ABI pip/MKL stack (moved from runtime): image-build -> MKL source
# numpy/numexpr/pandas -> rest wheels. Source builds also force-link jemalloc.
COPY pyproject.toml ./

# 1) Requirements slices from pyproject (no pip).
# Split mkl-src: numpy first, then numexpr (VML via site.cfg), then pandas.
# Under --no-build-isolation, numexpr setup.py imports numpy during metadata.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  python3 -c "import re, tomllib; from pathlib import Path; \
    n = lambda d: re.split(r\"[=<>~;!@[ ]\", d, maxsplit=1)[0].strip().lower(); \
    proj = tomllib.loads(Path(\"pyproject.toml\").read_text())[\"project\"]; \
    deps = proj[\"dependencies\"]; \
    build = proj[\"optional-dependencies\"][\"image-build\"]; \
    build_names = {n(d) for d in build}; \
    assert {\"mkl\", \"mkl-devel\", \"meson-python\", \"meson\", \"ninja\", \"cython\", \"setuptools\", \"versioneer\"} <= build_names, build_names; \
    src_names = {\"numpy\", \"numexpr\", \"pandas\"}; \
    src = [d for d in deps if n(d) in src_names]; \
    assert src_names <= {n(d) for d in src}, src_names - {n(d) for d in src}; \
    rest = [d for d in deps if n(d) not in src_names]; \
    numpy_reqs = [d for d in src if n(d) == \"numpy\"]; \
    numexpr_reqs = [d for d in src if n(d) == \"numexpr\"]; \
    pandas_reqs = [d for d in src if n(d) == \"pandas\"]; \
    after_numpy = numexpr_reqs + pandas_reqs; \
    assert numpy_reqs and numexpr_reqs and pandas_reqs, (numpy_reqs, numexpr_reqs, pandas_reqs); \
    Path(\"/tmp/requirements.txt\").write_text(\"\\n\".join(deps) + \"\\n\"); \
    Path(\"/tmp/requirements-rest.txt\").write_text(\"\\n\".join(rest) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-src.txt\").write_text(\"\\n\".join(src) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-numpy.txt\").write_text(\"\\n\".join(numpy_reqs) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-numexpr.txt\").write_text(\"\\n\".join(numexpr_reqs) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-pandas.txt\").write_text(\"\\n\".join(pandas_reqs) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-after-numpy.txt\").write_text(\"\\n\".join(after_numpy) + \"\\n\"); \
    Path(\"/tmp/requirements-build.txt\").write_text(\"\\n\".join(build) + \"\\n\")"'

# 2) GIL image-build wheels only (MKL + toolchain).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  export PATH="/opt/python3.14/bin:/usr/local/bin:/opt/zstd/bin:${PATH}"; \
  python3 -m pip install --no-cache-dir --upgrade pip; \
  python3 -m pip install --no-cache-dir -r /tmp/requirements-build.txt; \
  test "$(command -v cython)" = "/opt/python3.14/bin/cython"; \
  if command -v cython3 >/dev/null 2>&1; then \
    test "$(command -v cython3)" = "/opt/python3.14/bin/cython3"; \
  fi; \
  cython -V'

# 3) GIL source-build against MKL (before rest): numpy, numexpr+VML, pandas.
# mkl 2026+ wheels install shared libs under sysconfig data/lib (no Python package).
# np.show_config() prints and returns None by default — use mode=dicts for assert.
# numexpr VML requires site.cfg in the sdist tree (setup.py sets USE_VML).
# GNU ld -lmkl_rt needs unversioned libmkl_rt.so; pip mkl wheels often ship only .so.N.
# -march=native: images are built on the prod host that runs them (not portable).
# pandas meson generate_version.py imports versioneer (image-build extra).
# numexpr and pandas --no-deps: without it, pip resolves numpy>=… and replaces
# the MKL source build with a manylinux OpenBLAS wheel (same version string).
# python-dateutil (+ six) come from project.dependencies via the rest RUN.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  export PATH="/opt/python3.14/bin:/usr/local/bin:/opt/zstd/bin:${PATH}"; \
  test "$(command -v cython)" = "/opt/python3.14/bin/cython"; \
  if command -v cython3 >/dev/null 2>&1; then \
    test "$(command -v cython3)" = "/opt/python3.14/bin/cython3"; \
  fi; \
  MKLROOT="$(python3 -c "import sysconfig; from pathlib import Path; r=Path(sysconfig.get_path(\"data\")); assert list((r/\"lib\"/\"pkgconfig\").glob(\"mkl-*.pc\")), r; assert list((r/\"lib\").glob(\"libmkl_rt.so*\")), r; assert (r/\"include\"/\"mkl.h\").is_file() or list((r/\"include\").rglob(\"mkl.h\")), r; print(r)")"; \
  export MKLROOT PKG_CONFIG_PATH="${MKLROOT}/lib/pkgconfig" LD_LIBRARY_PATH="${MKLROOT}/lib"; \
  shopt -s nullglob; \
  if [ ! -e "${MKLROOT}/lib/libmkl_rt.so" ]; then \
    _mkl_vers=("${MKLROOT}"/lib/libmkl_rt.so.*); \
    test "${#_mkl_vers[@]}" -ge 1; \
    ln -s "$(basename "${_mkl_vers[0]}")" "${MKLROOT}/lib/libmkl_rt.so"; \
  fi; \
  test -e "${MKLROOT}/lib/libmkl_rt.so"; \
  export LIBRARY_PATH="${MKLROOT}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"; \
  export LDFLAGS="${LDFLAGS:+${LDFLAGS} }-L${MKLROOT}/lib -Wl,-rpath,${MKLROOT}/lib -L/opt/zlib-ng/lib -Wl,-rpath,/opt/zlib-ng/lib -L/opt/jemalloc/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,--no-as-needed -ljemalloc -Wl,--as-needed"; \
  export CFLAGS="${CFLAGS:+${CFLAGS} }-O3 -march=native -g0 -I/opt/zlib-ng/include" \
    CXXFLAGS="${CXXFLAGS:+${CXXFLAGS} }-O3 -march=native -g0 -I/opt/zlib-ng/include" \
    FFLAGS="${FFLAGS:+${FFLAGS} }-O3 -march=native -g0"; \
  python3 -m pip install --no-cache-dir --no-build-isolation --force-reinstall \
    --no-binary numpy \
    --config-settings=setup-args=-Dblas=mkl \
    --config-settings=setup-args=-Dlapack=mkl \
    --config-settings=setup-args=-Dcpu-baseline=native \
    --config-settings=setup-args=-Dcpu-dispatch=max \
    -r /tmp/requirements-mkl-numpy.txt; \
  python3 -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  rm -rf /tmp/numexpr-dl /tmp/numexpr-src; mkdir -p /tmp/numexpr-dl /tmp/numexpr-src; \
  python3 -m pip download --no-cache-dir --no-binary=:all: --no-deps \
    --no-build-isolation -d /tmp/numexpr-dl \
    -r /tmp/requirements-mkl-numexpr.txt; \
  shopt -s nullglob; \
  _ne_tars=(/tmp/numexpr-dl/numexpr-*.tar.gz); \
  test "${#_ne_tars[@]}" -eq 1; \
  tar -xzf "${_ne_tars[0]}" -C /tmp/numexpr-src; \
  _ne_srcs=(/tmp/numexpr-src/numexpr-*); \
  test "${#_ne_srcs[@]}" -eq 1; \
  NUMEXPR_SRC="${_ne_srcs[0]}"; \
  printf "%s\n" "[mkl]" "include_dirs = ${MKLROOT}/include" \
    "library_dirs = ${MKLROOT}/lib" "libraries = mkl_rt" > "${NUMEXPR_SRC}/site.cfg"; \
  python3 -m pip install --no-cache-dir --no-build-isolation --force-reinstall --no-deps \
    "${NUMEXPR_SRC}"; \
  python3 -c "import numexpr as ne; assert ne.use_vml is True, (ne.use_vml, getattr(ne, \"get_vml_version\", lambda: None)())"; \
  python3 -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  python3 -m pip install --no-cache-dir --no-build-isolation --force-reinstall --no-deps \
    --no-binary pandas \
    -r /tmp/requirements-mkl-pandas.txt; \
  python3 -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  echo "${MKLROOT}/lib" > /etc/ld.so.conf.d/mkl-gil.conf'

# 4) GIL rest wheels (Bokeh/contourpy see MKL numpy; constraint blocks wheel upgrades).
# python-dateutil is here; import pandas only after rest (needs dateutil).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  python3 -m pip install --no-cache-dir --constraint /tmp/requirements-mkl-src.txt \
    -r /tmp/requirements-rest.txt; \
  python3 -c "import pandas as pd; assert pd.__version__"'

# 5) Free-threaded image-build wheels only (pip already from --with-ensurepip=install).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  export PATH="/opt/python3.14t/bin:/usr/local/bin:/opt/zstd/bin:${PATH}"; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --upgrade pip; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir -r /tmp/requirements-build.txt; \
  test "$(command -v cython)" = "/opt/python3.14t/bin/cython"; \
  if command -v cython3 >/dev/null 2>&1; then \
    test "$(command -v cython3)" = "/opt/python3.14t/bin/cython3"; \
  fi; \
  cython -V'

# 6) Free-threaded source-build + ldconfig (numpy, numexpr+VML, pandas).
# Same as GIL: MKLROOT is sysconfig data prefix; numexpr VML via injected site.cfg.
# -march=native: prod-host builds only (same as GIL compile RUN).
# numexpr/pandas --no-deps: same MKL-numpy preservation as GIL (dateutil via rest).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  export PATH="/opt/python3.14t/bin:/usr/local/bin:/opt/zstd/bin:${PATH}"; \
  test "$(command -v cython)" = "/opt/python3.14t/bin/cython"; \
  if command -v cython3 >/dev/null 2>&1; then \
    test "$(command -v cython3)" = "/opt/python3.14t/bin/cython3"; \
  fi; \
  MKLROOT_T="$(/opt/python3.14t/bin/python3.14t -c "import sysconfig; from pathlib import Path; r=Path(sysconfig.get_path(\"data\")); assert list((r/\"lib\"/\"pkgconfig\").glob(\"mkl-*.pc\")), r; assert list((r/\"lib\").glob(\"libmkl_rt.so*\")), r; assert (r/\"include\"/\"mkl.h\").is_file() or list((r/\"include\").rglob(\"mkl.h\")), r; print(r)")"; \
  export MKLROOT="${MKLROOT_T}" PKG_CONFIG_PATH="${MKLROOT_T}/lib/pkgconfig" LD_LIBRARY_PATH="${MKLROOT_T}/lib"; \
  shopt -s nullglob; \
  if [ ! -e "${MKLROOT_T}/lib/libmkl_rt.so" ]; then \
    _mkl_vers=("${MKLROOT_T}"/lib/libmkl_rt.so.*); \
    test "${#_mkl_vers[@]}" -ge 1; \
    ln -s "$(basename "${_mkl_vers[0]}")" "${MKLROOT_T}/lib/libmkl_rt.so"; \
  fi; \
  test -e "${MKLROOT_T}/lib/libmkl_rt.so"; \
  export LIBRARY_PATH="${MKLROOT_T}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"; \
  export LDFLAGS="${LDFLAGS:+${LDFLAGS} }-L${MKLROOT_T}/lib -Wl,-rpath,${MKLROOT_T}/lib -L/opt/zlib-ng/lib -Wl,-rpath,/opt/zlib-ng/lib -L/opt/jemalloc/lib -Wl,-rpath,/opt/jemalloc/lib -Wl,--no-as-needed -ljemalloc -Wl,--as-needed"; \
  export CFLAGS="${CFLAGS:+${CFLAGS} }-O3 -march=native -g0 -I/opt/zlib-ng/include" \
    CXXFLAGS="${CXXFLAGS:+${CXXFLAGS} }-O3 -march=native -g0 -I/opt/zlib-ng/include" \
    FFLAGS="${FFLAGS:+${FFLAGS} }-O3 -march=native -g0"; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-build-isolation --force-reinstall \
    --no-binary numpy \
    --config-settings=setup-args=-Dblas=mkl \
    --config-settings=setup-args=-Dlapack=mkl \
    --config-settings=setup-args=-Dcpu-baseline=native \
    --config-settings=setup-args=-Dcpu-dispatch=max \
    -r /tmp/requirements-mkl-numpy.txt; \
  /opt/python3.14t/bin/python3.14t -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  rm -rf /tmp/numexpr-dl-t /tmp/numexpr-src-t; mkdir -p /tmp/numexpr-dl-t /tmp/numexpr-src-t; \
  /opt/python3.14t/bin/python3.14t -m pip download --no-cache-dir --no-binary=:all: \
    --no-deps --no-build-isolation \
    -d /tmp/numexpr-dl-t -r /tmp/requirements-mkl-numexpr.txt; \
  shopt -s nullglob; \
  _ne_tars=(/tmp/numexpr-dl-t/numexpr-*.tar.gz); \
  test "${#_ne_tars[@]}" -eq 1; \
  tar -xzf "${_ne_tars[0]}" -C /tmp/numexpr-src-t; \
  _ne_srcs=(/tmp/numexpr-src-t/numexpr-*); \
  test "${#_ne_srcs[@]}" -eq 1; \
  NUMEXPR_SRC="${_ne_srcs[0]}"; \
  printf "%s\n" "[mkl]" "include_dirs = ${MKLROOT_T}/include" \
    "library_dirs = ${MKLROOT_T}/lib" "libraries = mkl_rt" > "${NUMEXPR_SRC}/site.cfg"; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-build-isolation \
    --force-reinstall --no-deps "${NUMEXPR_SRC}"; \
  /opt/python3.14t/bin/python3.14t -c "import numexpr as ne; assert ne.use_vml is True, (ne.use_vml, getattr(ne, \"get_vml_version\", lambda: None)())"; \
  /opt/python3.14t/bin/python3.14t -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-build-isolation --force-reinstall --no-deps \
    --no-binary pandas \
    -r /tmp/requirements-mkl-pandas.txt; \
  /opt/python3.14t/bin/python3.14t -c "import numpy as np; c=np.show_config(mode=\"dicts\"); assert \"mkl\" in str(c).lower(), c"; \
  echo "${MKLROOT_T}/lib" > /etc/ld.so.conf.d/mkl-ft.conf; \
  ldconfig'

# 7) Free-threaded rest wheels (dateutil then import pandas, same as GIL).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir \
    --constraint /tmp/requirements-mkl-src.txt \
    -r /tmp/requirements-rest.txt; \
  /opt/python3.14t/bin/python3.14t -c "import pandas as pd; assert pd.__version__"'

# Install debugging tools into GIL prefix (py-spy / pyinstrument).
RUN /bin/bash -o pipefail -c 'python3 -m pip install --no-cache-dir pyinstrument py-spy'

# Uninstall image-build-only toolchain from both ABIs; keep runtime mkl; final strip.
# Prune compile leftovers under /opt so hpcperfstats-base never inherits src/build trees.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  python3 -m pip uninstall -y meson meson-python ninja cython versioneer mkl-devel || true; \
  /opt/python3.14t/bin/python3.14t -m pip uninstall -y meson meson-python ninja cython versioneer mkl-devel || true; \
  python3 -m pip cache purge; \
  /opt/python3.14t/bin/python3.14t -m pip cache purge; \
  for root in /opt/jemalloc /opt/zlib-ng /opt/zstd /opt/mpdecimal /opt/libffi /opt/python3.14 /opt/python3.14t; do \
    find "$root" -type f | while read -r f; do file -b "$f" | grep -q ELF && strip --strip-unneeded "$f" || true; done; \
  done; \
  for bin in /opt/python3.14/bin/python3.14 /opt/python3.14t/bin/python3.14t /opt/jemalloc/lib/libjemalloc.so.2; do \
    file -b "$bin" | grep -qi "not stripped" && { echo "unstripped $bin"; false; }; \
    if readelf -S "$bin" | grep -q "\.debug_info"; then echo "debug_info $bin"; false; fi; \
  done; \
  for root in /opt/jemalloc /opt/zlib-ng /opt/zstd /opt/mpdecimal /opt/libffi; do \
    find "$root" -type f \( -name "*.a" -o -name "*.la" -o -name "CMakeCache.txt" \) -delete; \
    find "$root" -type d \( -name build -o -name CMakeFiles -o -name .libs \) -prune -exec rm -rf {} + 2>/dev/null || true; \
  done; \
  if find /usr/src -mindepth 1 -print -quit 2>/dev/null | grep -q .; then \
    echo "leftover /usr/src compile tree would risk leaking into later stages"; find /usr/src -maxdepth 2; false; \
  fi; \
  test ! -d /usr/src/zlib-ng; \
  test ! -d /usr/src/zstd; \
  test ! -d /usr/src/jemalloc; \
  test ! -d /usr/src/python'

# Slim runtime: COPY only /opt install prefixes (never /usr/src or cmake build dirs).
FROM debian:trixie-slim AS hpcperfstats-base

RUN /bin/bash -o pipefail -c "useradd -u 901860 -ms /bin/bash hpcperfstats \
    && mkdir -p /hpcperfstats /home/hpcperfstats/.ssh \
        /var/lib/hpcperfstats-syslog \
    && chmod 700 /home/hpcperfstats/.ssh \
    && chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh"

# Runtime apt only (no compilers / *-dev).
RUN /bin/bash -o pipefail -c "apt-get update -y \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       supervisor rsync syslog-ng util-linux time \
       net-tools lsof procps gdb strace netcat-openbsd \
       vim nano ca-certificates \
       libssl3t64 libsqlite3-0 libbz2-1.0 liblzma5 \
       libreadline8t64 libncursesw6 libuuid1 libgdbm6t64 libexpat1 \
       libpq5 libmariadb3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*"

COPY --from=python-build /opt/jemalloc /opt/jemalloc
COPY --from=python-build /opt/zlib-ng /opt/zlib-ng
COPY --from=python-build /opt/zstd /opt/zstd
COPY --from=python-build /opt/mpdecimal /opt/mpdecimal
COPY --from=python-build /opt/libffi /opt/libffi
COPY --from=python-build /opt/python3.14 /opt/python3.14
COPY --from=python-build /opt/python3.14t /opt/python3.14t

# Default syslog-ng allowlist fragment (render overwrites at container start).
RUN /bin/bash -o pipefail -c "printf '%s\n' \
    '# Generated fallback (build-time default). Runtime startup rewrites this file.' \
    'source s_net {' \
    '       tcp(ip(0.0.0.0) port(514) max-connections (100) log_iw_size(100000)) ;' \
    '       udp(ip(0.0.0.0) port(514));' \
    '};' \
    '' \
    'filter f_hps_syslog_allow_net {' \
    '       netmask(0.0.0.0/0);' \
    '};' \
    > /var/lib/hpcperfstats-syslog/generated.conf \
    && chmod 644 /var/lib/hpcperfstats-syslog/generated.conf"

# Recreate GIL /usr/local symlinks, ldconfig paths, jemalloc preload (path A + B).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  mkdir -p /usr/local/bin /usr/local/lib; \
  ln -sfn /opt/python3.14/bin/python3 /usr/local/bin/python3; \
  ln -sfn /opt/python3.14/bin/python3.14 /usr/local/bin/python3.14; \
  ln -sfn /opt/python3.14/bin/pip3 /usr/local/bin/pip3; \
  ln -sfn /opt/python3.14/bin/pip3.14 /usr/local/bin/pip3.14; \
  if [ -e /opt/python3.14/bin/gunicorn ]; then ln -sfn /opt/python3.14/bin/gunicorn /usr/local/bin/gunicorn; fi; \
  for so in /opt/python3.14/lib/libpython3.14.so*; do ln -sfn "$so" "/usr/local/lib/$(basename "$so")"; done; \
  for b in zstd zstdcat zstdgrep zstdless zstdmt unzstd; do \
    if [ -e "/opt/zstd/bin/$b" ]; then \
      ln -sfn "/opt/zstd/bin/$b" "/usr/local/bin/$b"; \
      ln -sfn "/opt/zstd/bin/$b" "/usr/bin/$b"; \
    fi; \
  done; \
  echo "/opt/jemalloc/lib" > /etc/ld.so.conf.d/jemalloc.conf; \
  echo "/opt/zlib-ng/lib" > /etc/ld.so.conf.d/zlib-ng.conf; \
  echo "/opt/zstd/lib" > /etc/ld.so.conf.d/zstd.conf; \
  echo "/opt/mpdecimal/lib" > /etc/ld.so.conf.d/mpdecimal.conf; \
  if [ -d /opt/libffi/lib/x86_64-linux-gnu ]; then \
    echo "/opt/libffi/lib/x86_64-linux-gnu" > /etc/ld.so.conf.d/libffi.conf; \
  else \
    echo "/opt/libffi/lib" > /etc/ld.so.conf.d/libffi.conf; \
  fi; \
  echo "/opt/python3.14/lib" > /etc/ld.so.conf.d/python314.conf; \
  echo "/opt/python3.14t/lib" > /etc/ld.so.conf.d/python314t.conf; \
  echo "/opt/python3.14/lib" > /etc/ld.so.conf.d/mkl-gil.conf; \
  echo "/opt/python3.14t/lib" > /etc/ld.so.conf.d/mkl-ft.conf; \
  ldconfig; \
  JE_SO="/opt/jemalloc/lib/libjemalloc.so.2"; \
  test -f "${JE_SO}"; \
  printf "%s\n" "${JE_SO}" > /etc/ld.so.preload; \
  test -s /etc/ld.so.preload; \
  file -b "${JE_SO}" | grep -q ELF; \
  test -x /usr/local/bin/zstd; \
  test "$(readlink -f /usr/local/bin/zstd)" = "$(readlink -f /opt/zstd/bin/zstd)"; \
  /usr/local/bin/zstd --version | grep -F "1.5.7"
'

# Gunicorn stays GIL (/usr/local/bin/gunicorn). background_thread:false is fork-safe for prefork.
# Path B: LD_PRELOAD + ld.so.preload so manylinux wheels hit jemalloc (link-only is not enough).
ENV LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2 \
    MALLOC_CONF=background_thread:false,metadata_thp:auto \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini \
    STATIC_ROOT=/home/hpcperfstats/staticfiles \
    PATH=/usr/local/bin:/opt/zstd/bin:/opt/python3.14/bin:/opt/python3.14t/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /home/hpcperfstats

COPY --chown=hpcperfstats:hpcperfstats . .

# .git is in the build context for frontend-builder SPA bake only — do not ship history.
RUN rm -rf /home/hpcperfstats/.git

# Cloud-synced checkouts may not preserve host execute bits; compose invokes these
# scripts directly as container commands.
# django_startup.sh runs collectstatic + spa_static_root_heal on every web start:
# that fingerprint heal is what lands a new SPA into compose staticfiles_data after
# a from-scratch image rebuild (image collectstatic alone cannot write the volume).
RUN chmod +x \
    /home/hpcperfstats/services-conf/django_startup.sh \
    /home/hpcperfstats/services-conf/supervisor_startup.sh \
    /home/hpcperfstats/services-conf/rsync_data_wrapper.sh \
    /home/hpcperfstats/services-conf/rsync_data.sh \
    /home/hpcperfstats/services-conf/rsync_data.sh.example

# Install the hpcperfstats package (deps already installed in python-build) for both ABIs.
RUN /bin/bash -o pipefail -c "python3 -m pip install --no-cache-dir --no-deps . \
    && /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-deps . \
    && python3 -m pip cache purge \
    && /opt/python3.14t/bin/python3.14t -m pip cache purge"

# Pipeline-only refresh: scripts/rebuild_pipeline.sh populates
# .build/pipeline-rebuild-frontend/ from the live deployment before build.
# Must appear before hpcperfstats-full: podman-compose often ignores build.target
# and builds the last stage; rebuild_pipeline.sh passes --target explicitly.
FROM hpcperfstats-base AS hpcperfstats-pipeline-refresh

COPY .build/pipeline-rebuild-frontend/ \
    /home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend/
RUN chown -R hpcperfstats:hpcperfstats \
    /home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend

RUN /bin/bash -o pipefail -c "/usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput"

# Default image: npm-built frontend from frontend-builder (last stage = default target).
FROM hpcperfstats-base AS hpcperfstats-full

COPY --from=frontend-builder --chown=hpcperfstats:hpcperfstats \
    /tmp/frontend-static \
    /home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend

# Image collectstatic: fail-fast at build and for runs without a compose volume
# over STATIC_ROOT. Compose mounts named volume staticfiles_data at
# /home/hpcperfstats/staticfiles, which masks this image layer — build cannot
# sync the live volume. After recreate, django_startup.sh (above) runs
# collectstatic on the volume, then spa_static_root_heal fingerprint-compares
# package vs volume machine/index.html and replaces STATIC_ROOT/frontend on
# drift. Optional SPA-only hot path: scripts/rebuild_frontend.sh (no full rebuild).
RUN /bin/bash -o pipefail -c "/usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput"
