# Build frontend assets in a dedicated node stage.
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /home/hpcperfstats
COPY --chown=node:node . .
WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend
RUN /bin/bash -o pipefail -c "npm ci && npm run build \
    && cp node_modules/axe-core/axe.min.js /tmp/axe-core.min.js"
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
    && mkdir -p /hpcperfstats /hpcperfstatslog /home/hpcperfstats/.ssh \
    && chmod 700 /home/hpcperfstats/.ssh \
    && chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh \
    && apt-get update \
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
COPY --from=frontend-builder --chown=hpcperfstats:hpcperfstats \
    /tmp/axe-core.min.js \
    /home/hpcperfstats/hpcperfstats/site/machine/tests/support/axe-core.min.js

# Set python install variables.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

# Install Python dependencies and the hpcperfstats package.
RUN /bin/bash -o pipefail -c "pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && pip cache purge"
