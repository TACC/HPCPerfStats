# syntax=docker/dockerfile:1
# Musl GCC 16.2 toolchain exported at /opt/gcc-16 for db/proxy build stages.
# Build on the production CPU (-march=native consumers). Tag: hpcperfstats-gcc-musl:16.2
# Context: ./services-conf (same as db.Dockerfile), e.g. podman build -f gcc-alpine.Dockerfile .

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

COPY assert_gcc_min_version.sh /usr/local/bin/assert_gcc_min_version.sh
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
