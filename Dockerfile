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

# Free-threaded CPython 3.14.7 (cp314t) for pipeline daemons only.
# Official Hub has no python:3.14t-* tag; compile from the matching source tarball.
FROM python:3.14.7-trixie AS python-freethreaded
ENV PYTHON_VERSION=3.14.7 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_WARN_SCRIPT_LOCATION=false \
    PIP_ROOT_USER_ACTION=ignore 
RUN /bin/bash -o pipefail -c "\
    apt-get update -y \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libssl-dev zlib1g-dev libncursesw5-dev libffi-dev libsqlite3-dev \
       libreadline-dev libbz2-dev liblzma-dev tk-dev uuid-dev \
       libgdbm-dev libnss3-dev ca-certificates curl libmpdec-dev libmpdec++-dev \
    && curl -fsSL \"https://www.python.org/ftp/python/\${PYTHON_VERSION}/Python-\${PYTHON_VERSION}.tgz\" \
       -o /tmp/Python.tgz \
    && mkdir -p /usr/src/python \
    && tar -xzf /tmp/Python.tgz -C /usr/src/python --strip-components=1 \
    && rm -f /tmp/Python.tgz \
    && cd /usr/src/python \
    && ./configure \
         --prefix=/opt/python3.14t \
         --enable-shared \
         --with-ensurepip=install \
         --disable-gil \
         LDFLAGS=\"-Wl,-rpath=/opt/python3.14t/lib\" \
    && make -j\"\$(nproc)\" \
    && make install \
    && ln -sf python3.14t /opt/python3.14t/bin/python \
    && ln -sf python3.14t /opt/python3.14t/bin/python3 \
    && /opt/python3.14t/bin/python3.14t -c \"import sysconfig; assert int(sysconfig.get_config_var('Py_GIL_DISABLED') or 0) == 1\" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /usr/src/python"

# Shared Python runtime base (no frontend overlay, pip deps layered before full tree).
FROM python:3.14.7-trixie AS hpcperfstats-base

# Setup users, directories, and required runtime and debug packages.
# default-libmysqlclient-dev/pkg-config: mysqlclient has no manylinux cp314(t) wheel.
# libpq5: runtime for pure-Python psycopg.
RUN /bin/bash -o pipefail -c "useradd -u 901860 -ms /bin/bash hpcperfstats \
    && mkdir -p /hpcperfstats /home/hpcperfstats/.ssh \
        /var/lib/hpcperfstats-syslog \
    && chmod 700 /home/hpcperfstats/.ssh \
    && chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh \
    && apt-get update -y \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       supervisor rsync syslog-ng zstd util-linux time \
       net-tools lsof procps gdb strace netcat-openbsd \
       vim nano build-essential pkg-config \
       gfortran ninja-build cmake \
       default-libmysqlclient-dev libpq5 libmpdec4 \
       libmpdec++4 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*"

# Bake free-threaded prefix (required for listend/sync_timedb/update_metrics).
COPY --from=python-freethreaded /opt/python3.14t /opt/python3.14t


# Default syslog-ng allowlist fragment (render overwrites at container start).
# Keep this self-contained so image builds do not depend on optional files in
# services-conf/ that may be absent in some checkouts.
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

WORKDIR /home/hpcperfstats

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini \
    STATIC_ROOT=/home/hpcperfstats/staticfiles


# Upgrade pip and install python dependencies: cached until pyproject.toml changes.
# Dual ABI: GIL (/usr/local) for web/helpers; cp314t (/opt/python3.14t) for pipeline.
# Per ABI: image-build wheels (MKL + meson) -> source-build numpy/numexpr/pandas
# against MKL -> rest wheels (constrained so Bokeh cannot pull OpenBLAS numpy).
COPY --chown=hpcperfstats:hpcperfstats pyproject.toml ./

# 1) Requirements slices from pyproject (no pip).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  python3 -c "import re, tomllib; from pathlib import Path; \
    n = lambda d: re.split(r\"[=<>~;!@[ ]\", d, maxsplit=1)[0].strip().lower(); \
    proj = tomllib.loads(Path(\"pyproject.toml\").read_text())[\"project\"]; \
    deps = proj[\"dependencies\"]; \
    build = proj[\"optional-dependencies\"][\"image-build\"]; \
    build_names = {n(d) for d in build}; \
    assert {\"mkl\", \"mkl-devel\", \"meson-python\", \"setuptools\"} <= build_names, build_names; \
    src_names = {\"numpy\", \"numexpr\", \"pandas\"}; \
    src = [d for d in deps if n(d) in src_names]; \
    assert src_names <= {n(d) for d in src}, src_names - {n(d) for d in src}; \
    rest = [d for d in deps if n(d) not in src_names]; \
    Path(\"/tmp/requirements.txt\").write_text(\"\\n\".join(deps) + \"\\n\"); \
    Path(\"/tmp/requirements-rest.txt\").write_text(\"\\n\".join(rest) + \"\\n\"); \
    Path(\"/tmp/requirements-mkl-src.txt\").write_text(\"\\n\".join(src) + \"\\n\"); \
    Path(\"/tmp/requirements-build.txt\").write_text(\"\\n\".join(build) + \"\\n\")"'

# 2) GIL image-build wheels only (MKL + toolchain).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  pip install --no-cache-dir --upgrade pip; \
  pip install --no-cache-dir -r /tmp/requirements-build.txt'

# 3) GIL source-build numpy/numexpr/pandas against MKL (before rest).
# mkl 2026+ wheels install shared libs under sysconfig data/lib (no Python package).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  MKLROOT="$(python3 -c "import sysconfig; from pathlib import Path; r=Path(sysconfig.get_path(\"data\")); assert list((r/\"lib\"/\"pkgconfig\").glob(\"mkl-*.pc\")), r; assert list((r/\"lib\").glob(\"libmkl_rt.so*\")), r; print(r)")"; \
  export MKLROOT PKG_CONFIG_PATH="${MKLROOT}/lib/pkgconfig" LD_LIBRARY_PATH="${MKLROOT}/lib"; \
  pip install --no-cache-dir --no-build-isolation --force-reinstall \
    --no-binary numpy,numexpr,pandas \
    --config-settings=setup-args=-Dblas=mkl \
    --config-settings=setup-args=-Dlapack=mkl \
    -r /tmp/requirements-mkl-src.txt; \
  python3 -c "import numpy as np; c=str(np.show_config()); assert \"mkl\" in c.lower() or \"mkl_rt\" in c.lower(), c"; \
  echo "${MKLROOT}/lib" > /etc/ld.so.conf.d/mkl-gil.conf'

# 4) GIL rest wheels (Bokeh/contourpy see MKL numpy; constraint blocks wheel upgrades).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  pip install --no-cache-dir --constraint /tmp/requirements-mkl-src.txt \
    -r /tmp/requirements-rest.txt'

# 5) Free-threaded image-build wheels only (pip already from --with-ensurepip=install).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --upgrade pip; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir -r /tmp/requirements-build.txt'

# 6) Free-threaded source-build + ldconfig.
# Same as GIL: MKLROOT is sysconfig data prefix (shared libs only; no Python package).
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  MKLROOT_T="$(/opt/python3.14t/bin/python3.14t -c "import sysconfig; from pathlib import Path; r=Path(sysconfig.get_path(\"data\")); assert list((r/\"lib\"/\"pkgconfig\").glob(\"mkl-*.pc\")), r; assert list((r/\"lib\").glob(\"libmkl_rt.so*\")), r; print(r)")"; \
  export MKLROOT="${MKLROOT_T}" PKG_CONFIG_PATH="${MKLROOT_T}/lib/pkgconfig" LD_LIBRARY_PATH="${MKLROOT_T}/lib"; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-build-isolation --force-reinstall \
    --no-binary numpy,numexpr,pandas \
    --config-settings=setup-args=-Dblas=mkl \
    --config-settings=setup-args=-Dlapack=mkl \
    -r /tmp/requirements-mkl-src.txt; \
  /opt/python3.14t/bin/python3.14t -c "import numpy as np; c=str(np.show_config()); assert \"mkl\" in c.lower() or \"mkl_rt\" in c.lower(), c"; \
  echo "${MKLROOT_T}/lib" > /etc/ld.so.conf.d/mkl-ft.conf; \
  ldconfig'

# 7) Free-threaded rest wheels.
RUN /bin/bash -o pipefail -c '\
  set -euo pipefail; \
  /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir \
    --constraint /tmp/requirements-mkl-src.txt \
    -r /tmp/requirements-rest.txt'

# Install debugging tools.
RUN /bin/bash -o pipefail -c 'pip install --no-cache-dir pyinstrument py-spy'

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

# Install the hpcperfstats package (deps already installed above) for both ABIs.
RUN /bin/bash -o pipefail -c "pip install --no-cache-dir --no-deps . \
    && /opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --no-deps . \
    && pip cache purge \
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
