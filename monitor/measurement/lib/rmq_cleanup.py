"""Delete the ``<queue>_measure`` queue via the RabbitMQ management HTTP API (decision 12).

NONE / SOLO / CADENCE legs publish partial payloads to ``<site queue>_measure`` on the same
broker; the daemon declares that durable queue itself. This module refuses to touch any queue
that is not ``<production>_measure`` and, when the management API is unreachable or the conf
credentials lack permission, hands back the exact ``rabbitmqctl`` command for the broker admin.
The daemon always logs in to vhost ``/`` (``RMQ_VHOST`` in stats_buffer_rmq.c).
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MEASURE_SUFFIX = "_measure"
DAEMON_VHOST = "/"
DEFAULT_MGMT_PORT = 15672


def measure_queue_name(production_queue: str) -> str:
    return production_queue + MEASURE_SUFFIX


def _request(method: str, host: str, port: int, vhost: str, user: str, password: str,
             queue: str, timeout: float, suffix: str = "",
             body: dict[str, Any] | None = None) -> tuple[int, bytes]:
    url = (f"http://{host}:{port}/api/queues/{urllib.parse.quote(vhost, safe='')}/"
           f"{urllib.parse.quote(queue, safe='')}{suffix}")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read() if err.fp else b""


def _guard(queue: str, production_queue: str) -> None:
    if queue == production_queue or queue != measure_queue_name(production_queue):
        raise ValueError(f"refusing to touch queue {queue!r}: only "
                         f"{measure_queue_name(production_queue)!r} may be deleted")


def delete_queue_http(host: str, port: int, vhost: str, user: str, password: str, queue: str,
                      production_queue: str, timeout: float = 10.0) -> tuple[int, bytes]:
    _guard(queue, production_queue)
    return _request("DELETE", host, port, vhost, user, password, queue, timeout)


def admin_command(queue: str, vhost: str = DAEMON_VHOST) -> str:
    return f"rabbitmqctl delete_queue -p {vhost} {queue}"


def delete_measure_queue(conf: dict[str, str], mgmt_port: int = DEFAULT_MGMT_PORT,
                         timeout: float = 10.0) -> dict[str, Any]:
    prod = conf["queue"]
    queue = measure_queue_name(prod)
    try:
        status, _ = delete_queue_http(conf["server"], mgmt_port, DAEMON_VHOST, conf.get("user", ""),
                                      conf.get("password", ""), queue, prod, timeout)
    except (OSError, urllib.error.URLError) as err:
        return {"queue": queue, "deleted": False, "measure_queue_left": True,
                "reason": f"management API unreachable: {err}", "admin_command": admin_command(queue)}
    if status in (200, 204):
        return {"queue": queue, "deleted": True, "measure_queue_left": False}
    if status == 404:
        return {"queue": queue, "deleted": False, "measure_queue_left": False,
                "reason": "queue did not exist"}
    return {"queue": queue, "deleted": False, "measure_queue_left": True,
            "reason": f"management API returned HTTP {status}", "admin_command": admin_command(queue)}


def drain_measure_queue(conf: dict[str, str], mgmt_port: int = DEFAULT_MGMT_PORT,
                        count: int = 2000, truncate: int = 4 << 20,
                        timeout: float = 30.0) -> dict[str, Any]:
    """Consume up to ``count`` messages from ``<queue>_measure`` (never the production queue).

    Returns ``{status, messages}``; ``status`` is ``ok``, or a reason the payloads could not
    be read (management API unreachable / HTTP error), recorded as NOT_MEASURED upstream.
    """
    prod = conf["queue"]
    queue = measure_queue_name(prod)
    _guard(queue, prod)
    body = {"count": count, "ackmode": "ack_requeue_false", "encoding": "auto",
            "truncate": truncate}
    try:
        status, raw = _request("POST", conf["server"], mgmt_port, DAEMON_VHOST,
                               conf.get("user", ""), conf.get("password", ""), queue, timeout,
                               suffix="/get", body=body)
    except (OSError, urllib.error.URLError) as err:
        return {"status": f"management API unreachable: {err}", "messages": []}
    if status != 200:
        return {"status": f"management API returned HTTP {status}", "messages": []}
    try:
        docs = json.loads(raw or b"[]")
    except json.JSONDecodeError:
        return {"status": "management API returned non-JSON", "messages": []}
    return {"status": "ok", "messages": [str(d.get("payload") or "") for d in docs]}


def measure_queue_preflight(conf: dict[str, str], mgmt_port: int = DEFAULT_MGMT_PORT,
                            timeout: float = 10.0) -> dict[str, Any]:
    """Refuse when ``<queue>_measure`` already exists with a consumer (someone else's queue)."""
    prod = conf["queue"]
    if prod.endswith(MEASURE_SUFFIX):
        return {"ok": False, "reason": f"site queue {prod!r} already ends in {MEASURE_SUFFIX}"}
    queue = measure_queue_name(prod)
    try:
        status, body = _request("GET", conf["server"], mgmt_port, DAEMON_VHOST,
                                conf.get("user", ""), conf.get("password", ""), queue, timeout)
    except (OSError, urllib.error.URLError) as err:
        return {"ok": True, "mgmt_reachable": False,
                "reason": f"management API unreachable ({err}); consumer check skipped"}
    if status == 200:
        try:
            doc = json.loads(body or b"{}")
        except json.JSONDecodeError:
            doc = {}
        if int(doc.get("consumers") or 0) > 0:
            return {"ok": False, "mgmt_reachable": True,
                    "reason": f"queue {queue!r} exists with {doc['consumers']} consumer(s)"}
        return {"ok": True, "mgmt_reachable": True, "existing": True}
    return {"ok": True, "mgmt_reachable": status != 401, "existing": False, "http_status": status}
