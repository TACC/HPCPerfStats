# Build frontend assets in a dedicated node stage.
# COPY is scoped to frontend inputs so Python/backend changes do not bust npm layers.
FROM node:26.3.0-alpine3.23 AS frontend-builder
RUN apk add --no-cache bash
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
COPY --chown=node:node hpcperfstats.ini* ./

WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend

# Frontend source: cached until site/frontend changes.
COPY --chown=node:node hpcperfstats/site/frontend/ ./

RUN /bin/bash -o pipefail -c "npm run build"

WORKDIR /home/hpcperfstats
RUN /bin/bash -o pipefail -c "\
    mkdir -p /tmp/frontend-static && \
    cp -a hpcperfstats/site/hpcperfstats_site/static/frontend/. /tmp/frontend-static/"

# Shared Python runtime base (no frontend overlay, pip deps layered before full tree).
FROM python:3.12.13-trixie AS hpcperfstats-base

# Setup users, directories, and required runtime packages.
RUN /bin/bash -o pipefail -c "useradd -u 901860 -ms /bin/bash hpcperfstats \
    && mkdir -p /hpcperfstats /home/hpcperfstats/.ssh \
        /var/lib/hpcperfstats-syslog \
    && chmod 700 /home/hpcperfstats/.ssh \
    && chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh \
    && apt-get update -y \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       netcat-openbsd supervisor rsync syslog-ng util-linux \
       vim net-tools lsof zstd nano \
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

# Python dependencies: cached until pyproject.toml changes.
COPY --chown=hpcperfstats:hpcperfstats pyproject.toml ./
RUN /bin/bash -o pipefail -c 'pip install --no-cache-dir --upgrade pip \
    && python3 -c "import tomllib; from pathlib import Path; deps=tomllib.loads(Path(\"pyproject.toml\").read_text())[\"project\"][\"dependencies\"]; Path(\"/tmp/requirements.txt\").write_text(\"\\n\".join(deps)+\"\\n\")" \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip cache purge'

COPY --chown=hpcperfstats:hpcperfstats . .

# Cloud-synced checkouts may not preserve host execute bits; compose invokes these
# scripts directly as container commands.
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

# collectstatic here: fail-fast at image build (and supports runs without a
# compose volume over STATIC_ROOT). Compose still runs collectstatic on web
# startup because staticfiles_data masks the image layer at /home/hpcperfstats/staticfiles.
RUN /bin/bash -o pipefail -c "/usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput"
