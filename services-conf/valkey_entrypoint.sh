#!/bin/sh
# Prepare the shared Unix socket directory, then hand off to the official
# Valkey image entrypoint (root -> valkey user when argv[0] is valkey-server).
set -eu
mkdir -p /run/valkey
chmod 1777 /run/valkey
exec docker-entrypoint.sh "$@"
