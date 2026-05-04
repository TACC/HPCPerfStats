# Build frontend assets in a dedicated node stage.
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /home/hpcperfstats
COPY --chown=node:node . .
WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend
# Vite and @vitejs/plugin-react are devDependencies; they must be present to build.
# After `npm run build`, drop dev packages with the same effect as `npm ci --omit=dev`.
RUN /bin/bash -o pipefail -c "npm ci && npm run build && npm prune --omit=dev"
WORKDIR /home/hpcperfstats
RUN /bin/bash -o pipefail -c "\
    mkdir -p /tmp/frontend-static && \
    if [ -d \
\"/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend\" \
    ]; then \
      cp -a \
\"/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend/.\" \
        /tmp/frontend-static/; \
    fi && \
    rm -rf /home/hpcperfstats/hpcperfstats/site/frontend"

# Runtime image.
FROM python:3.12-trixie

# Setup users, directories, and required runtime packages.
RUN /bin/bash -o pipefail -c "useradd -u 901860 -ms /bin/bash hpcperfstats \
    && mkdir -p /hpcperfstats /home/hpcperfstats/.ssh \
        /var/lib/hpcperfstats-syslog \
    && chmod 700 /home/hpcperfstats/.ssh \
    && chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh \
    && apt-get update -y \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       netcat-openbsd supervisor rsync syslog-ng \
       vim net-tools lsof pigz nano \
    && rm -rf /var/lib/apt/lists/*"

WORKDIR /home/hpcperfstats

# Copy source, then overlay built frontend artifacts from the builder image.
COPY --chown=hpcperfstats:hpcperfstats . .
COPY --from=frontend-builder --chown=hpcperfstats:hpcperfstats \
    /tmp/frontend-static \
    /home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend

# Default syslog-ng allowlist fragment (render overwrites at container start).
RUN cp /home/hpcperfstats/services-conf/syslog-ng.d/10-hpcperfstats-generated.conf \
    /var/lib/hpcperfstats-syslog/generated.conf \
    && chmod 644 /var/lib/hpcperfstats-syslog/generated.conf

# Set python install variables.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini \
    STATIC_ROOT=/home/hpcperfstats/staticfiles

# Install Python dependencies and the hpcperfstats package.
# collectstatic here: fail-fast at image build (and supports runs without a
# compose volume over STATIC_ROOT). Compose still runs collectstatic on web
# startup because staticfiles_data masks the image layer at /home/hpcperfstats/staticfiles.
RUN /bin/bash -o pipefail -c "pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && /usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput \
    && pip cache purge"
