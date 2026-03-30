# Build frontend assets in a dedicated node stage.
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /home/hpcperfstats
COPY --chown=node:node . .
WORKDIR /home/hpcperfstats/hpcperfstats/site/frontend
RUN npm ci && npm run build
WORKDIR /home/hpcperfstats
RUN rm -rf /home/hpcperfstats/hpcperfstats/site/frontend

# Runtime image.
FROM python:3.12-trixie
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Setup users, directories, and required runtime packages.
RUN useradd -u 901860 -ms /bin/bash hpcperfstats && \
    mkdir -p /hpcperfstats /hpcperfstatslog /home/hpcperfstats/.ssh && \
    chmod 700 /home/hpcperfstats/.ssh && \
    chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        netcat-openbsd supervisor rsync syslog-ng vim net-tools lsof pigz nano && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/hpcperfstats

# Copy source and built frontend artifacts from the builder image.
COPY --from=frontend-builder --chown=hpcperfstats:hpcperfstats /home/hpcperfstats /home/hpcperfstats

# Set python install variables.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

# Install Python dependencies and the hpcperfstats package.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip cache purge
