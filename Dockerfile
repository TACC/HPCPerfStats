# Build frontend assets in a dedicated node stage.
# COPY is scoped to frontend inputs so Python/backend changes do not bust npm layers.
FROM node:26.5.0-alpine3.23 AS frontend-builder
RUN apk add --no-cache bash git
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

# Context .git for write-site-identity git rev-parse (checkout root = /home/hpcperfstats).
# Optional ARG/ENV override still wins when set to a real SHA (helpers / CI).
COPY .git /home/hpcperfstats/.git
ARG HPCPERFSTATS_GIT_COMMIT=unknown
ENV HPCPERFSTATS_GIT_COMMIT=$HPCPERFSTATS_GIT_COMMIT

RUN /bin/bash -o pipefail -c "npm run build:prod"

WORKDIR /home/hpcperfstats
RUN /bin/bash -o pipefail -c "\
    mkdir -p /tmp/frontend-static && \
    cp -a hpcperfstats/site/hpcperfstats_site/static/frontend/. /tmp/frontend-static/"

# Shared Python runtime base (no frontend overlay, pip deps layered before full tree).
FROM python:3.12.13-trixie AS hpcperfstats-base

# Setup users, directories, and required runtime and debug packages.
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
       vim nano \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*"

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

# Upgrade pip and install debugging tools.
RUN /bin/bash -o pipefail -c 'pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir pyinstrument py-spy'

# Python dependencies: cached until pyproject.toml changes.
COPY --chown=hpcperfstats:hpcperfstats pyproject.toml ./
RUN /bin/bash -o pipefail -c 'python3 -c "import tomllib; from pathlib import Path; deps=tomllib.loads(Path(\"pyproject.toml\").read_text())[\"project\"][\"dependencies\"]; Path(\"/tmp/requirements.txt\").write_text(\"\\n\".join(deps)+\"\\n\")" \
    && pip install --no-cache-dir -r /tmp/requirements.txt'

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
    /home/hpcperfstats/services-conf/supervisor_startup.sh

# Install the hpcperfstats package (deps already installed above).
RUN /bin/bash -o pipefail -c "pip install --no-cache-dir --no-deps . && pip cache purge"

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
