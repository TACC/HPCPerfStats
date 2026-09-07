#!/bin/sh
# Prepare the shared Unix socket directory, then hand off to the official
# Redis image entrypoint (root → redis user).
set -eu
mkdir -p /run/redis
chmod 1777 /run/redis
exec docker-entrypoint.sh "$@"
