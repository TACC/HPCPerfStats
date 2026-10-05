# syntax=docker/dockerfile:1
# Musl GCC 16.2 toolchain exported at /opt/gcc-16 for db/proxy build stages.
# Build on the production CPU (-march=native consumers). Tag: hpcperfstats-gcc-musl:16.2
# Context: ./services-conf, e.g. podman build -f gcc-alpine.Dockerfile .
# Assert gate is embedded below (must match services-conf/assert_gcc_min_version.sh).

ARG ALPINE_VERSION=3.24.2
ARG GCC_VERSION=16.2.0
ARG GCC_SHA256=e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e
ARG GCC_MIN_VERSION=16.2

FROM alpine:${ALPINE_VERSION} AS gcc-builder

ARG GCC_VERSION
ARG GCC_SHA256
ARG GCC_MIN_VERSION

RUN set -eux; \
  apk add --no-cache \
    bash \
    build-base \
    bison \
    curl \
    flex \
    gawk \
    gmp-dev \
    isl-dev \
    linux-headers \
    mpc1-dev \
    mpfr-dev \
    perl \
    texinfo \
    zlib-dev

COPY <<'ASSERT_GCC_EOF' /usr/local/bin/assert_gcc_min_version.sh
#!/usr/bin/env bash
# Fail closed when the active gcc is older than GCC_MIN_VERSION (e.g. 16.2).
set -euo pipefail

min="${GCC_MIN_VERSION:-16.2}"
if [[ $# -ge 1 ]]; then
  min="$1"
fi

if ! command -v gcc >/dev/null 2>&1; then
  echo "assert_gcc_min_version: gcc not found in PATH" >&2
  exit 1
fi

got="$(gcc -dumpfullversion)"
if [[ -z "${got}" ]]; then
  echo "assert_gcc_min_version: gcc -dumpfullversion returned empty" >&2
  exit 1
fi

# BusyBox sort (Alpine) has -c -V but not GNU -C; compare via sort -V only.
if [[ "$(printf '%s\n%s\n' "${min}" "${got}" | sort -V | head -n1)" != "${min}" ]]; then
  echo "assert_gcc_min_version: need gcc >= ${min}, got ${got}" >&2
  exit 1
fi

echo "assert_gcc_min_version: gcc ${got} >= ${min}"
ASSERT_GCC_EOF
RUN chmod +x /usr/local/bin/assert_gcc_min_version.sh

WORKDIR /usr/src

RUN set -eux; \
  curl -fsSL "https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/gcc-${GCC_VERSION}.tar.xz" \
    -o "gcc-${GCC_VERSION}.tar.xz"; \
  echo "${GCC_SHA256}  gcc-${GCC_VERSION}.tar.xz" | sha256sum -c -; \
  tar -xf "gcc-${GCC_VERSION}.tar.xz"; \
  cd "gcc-${GCC_VERSION}"; \
  ./contrib/download_prerequisites; \
  mkdir -p /usr/src/gcc-build; \
  cd /usr/src/gcc-build; \
  ../gcc-${GCC_VERSION}/configure \
    --prefix=/opt/gcc-16 \
    --disable-multilib \
    --disable-bootstrap \
    --enable-languages=c,c++ \
    --with-system-zlib; \
  make -j"$(nproc)"; \
  make install-strip; \
  rm -rf /usr/src/gcc-${GCC_VERSION} /usr/src/gcc-build /usr/src/gcc-${GCC_VERSION}.tar.xz

RUN PATH="/opt/gcc-16/bin:${PATH}" GCC_MIN_VERSION="${GCC_MIN_VERSION}" assert_gcc_min_version.sh

FROM alpine:${ALPINE_VERSION} AS gcc-export

ARG GCC_MIN_VERSION=16.2

COPY --from=gcc-builder /opt/gcc-16 /opt/gcc-16
COPY --from=gcc-builder /usr/local/bin/assert_gcc_min_version.sh /usr/local/bin/assert_gcc_min_version.sh

RUN chmod +x /usr/local/bin/assert_gcc_min_version.sh \
  && PATH="/opt/gcc-16/bin:${PATH}" GCC_MIN_VERSION="${GCC_MIN_VERSION}" assert_gcc_min_version.sh
