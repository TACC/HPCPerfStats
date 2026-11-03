"""Measure-queue cleanup through the RabbitMQ management API (stub HTTP server)."""

from __future__ import annotations

import base64
import http.server
import json
import threading

import pytest
from lib import rmq_cleanup


class _Handler(http.server.BaseHTTPRequestHandler):
    calls: list = []
    status = 204
    queue_doc = None

    def _reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        type(self).calls.append(("DELETE", self.path, self.headers.get("Authorization")))
        self._reply(type(self).status)

    def do_GET(self):
        type(self).calls.append(("GET", self.path, self.headers.get("Authorization")))
        if type(self).queue_doc is None:
            self._reply(404, b'{"error":"Object Not Found"}')
        else:
            self._reply(200, json.dumps(type(self).queue_doc).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.calls = []
    _Handler.status = 204
    _Handler.queue_doc = None
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


def _conf(port):
    return {"server": "127.0.0.1", "queue": "stampede3", "user": "monitor", "password": "pw"}


def test_measure_queue_cleanup(server):
    port = server.server_address[1]
    res = rmq_cleanup.delete_measure_queue(_conf(port), mgmt_port=port)
    assert res["deleted"] is True and res["measure_queue_left"] is False
    method, path, auth = _Handler.calls[-1]
    assert method == "DELETE" and path == "/api/queues/%2F/stampede3_measure"
    assert base64.b64decode(auth.split()[1]).decode() == "monitor:pw"


def test_measure_queue_cleanup_unauthorized_prints_rabbitmqctl(server):
    _Handler.status = 401
    res = rmq_cleanup.delete_measure_queue(_conf(0), mgmt_port=server.server_address[1])
    assert res["measure_queue_left"] is True
    assert res["admin_command"] == "rabbitmqctl delete_queue -p / stampede3_measure"


def test_measure_queue_cleanup_connection_refused():
    res = rmq_cleanup.delete_measure_queue(_conf(0), mgmt_port=1, timeout=2)
    assert res["measure_queue_left"] is True and "rabbitmqctl delete_queue" in res["admin_command"]


def test_production_queue_never_deleted(server):
    with pytest.raises(ValueError):
        rmq_cleanup.delete_queue_http("127.0.0.1", server.server_address[1], "/", "monitor", "pw",
                                      "stampede3", production_queue="stampede3")
    with pytest.raises(ValueError):
        rmq_cleanup.delete_queue_http("127.0.0.1", server.server_address[1], "/", "monitor", "pw",
                                      "other", production_queue="stampede3")
    assert _Handler.calls == []


def test_preflight_refuses_consumed_measure_queue(server):
    port = server.server_address[1]
    assert rmq_cleanup.measure_queue_preflight(_conf(port), mgmt_port=port)["ok"] is True
    _Handler.queue_doc = {"name": "stampede3_measure", "consumers": 1}
    res = rmq_cleanup.measure_queue_preflight(_conf(port), mgmt_port=port)
    assert res["ok"] is False and "consumer" in res["reason"]
    _Handler.queue_doc = {"name": "stampede3_measure", "consumers": 0}
    assert rmq_cleanup.measure_queue_preflight(_conf(port), mgmt_port=port)["ok"] is True
