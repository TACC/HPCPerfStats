"""Unit tests for Redis monitor_identity parse/set helpers."""

from __future__ import annotations

import json

from hpcperfstats.lib import monitor_identity as mi


def test_parse_dollar_message_with_build_and_schema():
    body = (
        "$\n"
        "1 node1.example.com\n"
        "$hpcperfstats 3.0\n"
        "$hostname node1.example.com\n"
        "$uname Linux aarch64 6.1.0 #1\n"
        "$build arch_aarch64_ver_3.0_debug\n"
        "!host_cpu user,E system,E\n"
        "!host_mem used,E\n"
        "!host_ib xmit,E\n"
    )
    ident = mi.parse_monitor_identity_from_dollar_message(
        body, updated_at=1_700_000_000
    )
    assert ident is not None
    assert ident["fqdn"] == "node1.example.com"
    assert ident["package_version"] == "3.0"
    assert ident["uname"] == "Linux aarch64 6.1.0 #1"
    assert ident["capability_slug"] == "arch_aarch64_ver_3.0_debug"
    assert ident["schema_types"] == ["host_cpu", "host_mem", "host_ib"]
    assert ident["updated_at"] == 1_700_000_000


def test_parse_dollar_message_tolerates_missing_build():
    body = (
        "$\n"
        "1 node1.example.com\n"
        "$hpcperfstats 2.9\n"
        "$uname Linux x86_64\n"
        "!host_cpu user,E\n"
    )
    ident = mi.parse_monitor_identity_from_dollar_message(body, updated_at=1)
    assert ident is not None
    assert ident["capability_slug"] is None
    assert ident["package_version"] == "2.9"
    assert ident["schema_types"] == ["host_cpu"]


def test_parse_rejects_non_fqdn_and_non_dollar():
    assert mi.parse_monitor_identity_from_dollar_message("not-dollar") is None
    assert (
        mi.parse_monitor_identity_from_dollar_message("$\n1 shortname\n") is None
    )


def test_set_and_load_monitor_identity_roundtrip():
    store: dict[str, str] = {}

    class FakeRedis:
        def set(self, name, value, ex=None):
            store[name] = value
            store["_ex"] = ex

        def get(self, name):
            return store.get(name)

    ident = {
        "fqdn": "a.example.com",
        "package_version": "3.0",
        "uname": "Linux",
        "capability_slug": None,
        "schema_types": ["host_cpu"],
        "updated_at": 42,
    }
    mi.set_monitor_identity(FakeRedis(), ident, ttl_seconds=99)
    assert store["_ex"] == 99
    loaded = mi.load_monitor_identities_for_hosts(
        FakeRedis(), ["a.example.com", "missing.example.com"]
    )
    assert len(loaded) == 1
    assert loaded[0]["fqdn"] == "a.example.com"
    assert loaded[0]["capability_slug"] is None
    assert json.loads(store[mi.monitor_identity_redis_key("a.example.com")])[
        "schema_types"
    ] == ["host_cpu"]
