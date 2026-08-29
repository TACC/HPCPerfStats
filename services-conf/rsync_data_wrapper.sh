#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HERE/rsync_data.sh" ]; then
    exec /bin/bash "$HERE/rsync_data.sh"
fi
if [ -f "$HERE/rsync_data.sh.example" ]; then
    exec /bin/bash "$HERE/rsync_data.sh.example"
fi
echo "rsync_data.sh and rsync_data.sh.example missing" >&2
exit 1
