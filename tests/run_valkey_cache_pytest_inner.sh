#!/usr/bin/env bash
# Invoked inside the web container by tests/run_valkey_cache_pytest_workflow.sh.
# The workflow streams this script to ``bash -s`` on stdin, so child commands
# read /dev/null instead of consuming the rest of the script.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
export HPCPERFSTATS_PYTEST_LIVE_VALKEY=1

compose_inner_pip_install

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

python3 -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_valkey_cache_live.py \
  "${ARGS[@]}" </dev/null

# Free-threaded check: the production django-valkey backend must import and
# round-trip under /opt/python3.14t without re-enabling the GIL (a C extension
# that lacks Py_mod_gil support re-enables it and emits a RuntimeWarning).
echo "Running free-threaded Valkey cache check (/opt/python3.14t)..."
DJANGO_SETTINGS_MODULE=hpcperfstats.site.hpcperfstats_site.settings \
  /opt/python3.14t/bin/python3 -W error::RuntimeWarning -c '
import sys
import uuid

import django

django.setup()

from django.conf import settings
from django.core.cache import cache

from hpcperfstats.site.lib.machine.cache_utils import get_valkey_client

backend = settings.CACHES["default"]["BACKEND"]
assert backend == "django_valkey.cache.ValkeyCache", backend
key = "valkey_ft_check:" + uuid.uuid4().hex
value = {"probe": key}
cache.set(key, value, timeout=60)
got = cache.get(key)
cache.delete(key)
assert got == value, got
client = get_valkey_client()
assert client is not None, "get_valkey_client() returned None"
assert client.ping() is True
assert sys._is_gil_enabled() is False, "GIL re-enabled under 3.14t"
print("VALKEY_FT_GIL_OFF")
' </dev/null
