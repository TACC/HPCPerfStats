"""Next-round gaps from the c122-018 2026-11-05 profile (a7c1ccca vs bfc7e897).

1. SCF cost was not isolated: SOLO(perf_event) ran arm_aarch64_imc without the overlay (it only
   waited), and the overlay programs the SCF counters inside dcgm_cpu either way. Overlay
   builds now put arm_aarch64_imc in dcgm_cpu and pair SOLO(dcgm_cpu) with a VARIANT leg that
   sets HPCPERFSTATS_LIKWID_GRACE_SCF=0, so SCF = SOLO(dcgm_cpu) − VARIANT(dcgm_cpu_noscf).
2. NVLink keys could not be checked (no payload capture). Legs on <queue>_measure now keep the
   last $ schema message; profile.json records keys per type; two runs diff with
   bin/compare_schema_keys.py.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lib import (
    collector_matrix,
    driver_map,
    orchestrator,
    rmq_cleanup,
    schedule,
    schema_capture,
)

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = {"arch": "aarch64", "configure": {"cpu_backend": "dcgm", "likwid_overlay": True}}
NO_OVERLAY = {"arch": "aarch64", "configure": {"cpu_backend": "dcgm"}}
ARM_ACTIVE = ["arm_aarch64_imc", "host_cpu", "host_cpu_hw", "nvidia_gpu"]
MARKER = driver_map.VARIANTS["dcgm_cpu_noscf"]["marker"]


def test_overlay_build_groups_imc_with_dcgm_cpu_and_offers_noscf_variant():
    groups = driver_map.groups_for_enabled(ARM_ACTIVE, OVERLAY)
    assert groups["dcgm_cpu"] == ["arm_aarch64_imc", "host_cpu_hw"]
    assert "perf_event" not in groups
    variants = driver_map.variants_for(groups, OVERLAY)
    assert list(variants) == ["dcgm_cpu_noscf"]
    v = variants["dcgm_cpu_noscf"]
    assert v["base"] == "dcgm_cpu" and v["types"] == ["host_cpu_hw"]
    assert v["env"] == {"HPCPERFSTATS_LIKWID_GRACE_SCF": "0"}
    # Must match the daemon's log line (cpu_counter_metrics_likwid_overlay.c).
    src = (ROOT.parent / "src" / "cpu_counter_metrics_likwid_overlay.c").read_text()
    assert '"off (HPCPERFSTATS_LIKWID_GRACE_SCF=0)"' in src and "SCF uncore events %s" in src
    assert MARKER == "SCF uncore events off (HPCPERFSTATS_LIKWID_GRACE_SCF=0)"


def test_non_overlay_builds_keep_perf_event_and_no_variant():
    groups = driver_map.groups_for_enabled(ARM_ACTIVE, NO_OVERLAY)
    assert groups["perf_event"] == ["arm_aarch64_imc"]
    assert driver_map.variants_for(groups, NO_OVERLAY) == {}
    # Overlay build without arm_aarch64_imc active: nothing to pair.
    g2 = driver_map.groups_for_enabled(["host_cpu_hw", "host_cpu"], OVERLAY)
    assert driver_map.variants_for(g2, OVERLAY) == {}


def test_schedule_places_variant_beside_solo_in_every_solo_block():
    groups = driver_map.groups_for_enabled(ARM_ACTIVE, OVERLAY)
    variants = driver_map.variants_for(groups, OVERLAY)
    plan = schedule.build_schedule(list(groups), blocks=2, seed=7, variants=variants,
                                   max_hours=48)
    var = [leg for leg in plan["legs"] if leg["kind"] == "VARIANT"]
    assert [leg["leg_id"] for leg in var] == ["b1-VARIANT-dcgm_cpu_noscf", "b2-VARIANT-dcgm_cpu_noscf"]
    assert all(leg["base"] == "dcgm_cpu" and leg["marker"] == MARKER for leg in var)
    assert all(leg["env"] == {"HPCPERFSTATS_LIKWID_GRACE_SCF": "0"} and not leg["fwq"] for leg in var)
    for b in (1, 2):
        kinds = [leg["kind"] for leg in plan["legs"] if leg["block"] == b]
        assert "VARIANT" in kinds and kinds.index("VARIANT") > kinds.index("ALL")
    assert plan["variants"] == ["dcgm_cpu_noscf"]
    # Same seed, no variants: SOLO legs unchanged in count (x86 / non-overlay schedules).
    plain = schedule.build_schedule(list(groups), blocks=2, seed=7, max_hours=48)
    assert not any(leg["kind"] == "VARIANT" for leg in plain["legs"])


def test_variant_dropin_keeps_host_cpu_hw_only_and_sets_knob():
    groups = driver_map.groups_for_enabled(ARM_ACTIVE, OVERLAY)
    plan = {"variant_specs": driver_map.variants_for(groups, OVERLAY)}
    lg = orchestrator.leg_groups(groups, plan)
    text = orchestrator.dropin_text("VARIANT", ARM_ACTIVE, lg, "dcgm_cpu_noscf",
                                    env={"HPCPERFSTATS_LIKWID_GRACE_SCF": "0"})
    assert "Environment=HPCPERFSTATS_DISABLE_TYPES=arm_aarch64_imc,host_cpu,nvidia_gpu\n" in text
    assert "Environment=HPCPERFSTATS_LIKWID_GRACE_SCF=0\n" in text
    assert orchestrator.MEASURE_CONF in text  # partial payloads stay off the production queue
    solo = orchestrator.dropin_text("SOLO", ARM_ACTIVE, lg, "dcgm_cpu")
    assert "GRACE_SCF" not in solo
    assert "HPCPERFSTATS_DISABLE_TYPES=host_cpu,nvidia_gpu\n" in solo


def _ci(mean, hw=0.5):
    return {"mean": mean, "lo": mean - hw, "hi": mean + hw, "n": 1800}


def _leg(kind, block, group=None, cpu=0.0, fds=0, **extra):
    proc = {"cpu_core_s_per_h": _ci(cpu), "uss_mib": 10.0, "broker_bytes_acked_per_h": 1.0,
            "wakeups_per_s": 1.0, "perf_event_fds": fds, "init_cpu_s": 0.1,
            "ms_per_fast_sample": 1.0, "ms_per_full_sample": 1.0}
    return {"leg_id": f"b{block}-{kind}-{group or ''}", "kind": kind, "block": block,
            "group": group, "status": "ok", "verified_types": extra.pop("types", []),
            "missing_types": [], "process": proc, "node": {"busy_cores": _ci(0.01, 0.001)},
            **extra}


def _matrix_legs(marker_seen=True):
    legs = []
    for b in (1, 2):
        legs += [_leg("NONE", b, cpu=2.0), _leg("ALL", b, cpu=130.0, types=ARM_ACTIVE),
                 _leg("SOLO", b, "dcgm_cpu", cpu=123.0, fds=292,
                      types=["arm_aarch64_imc", "host_cpu_hw"]),
                 _leg("VARIANT", b, "dcgm_cpu_noscf", cpu=122.4, fds=288, types=["host_cpu_hw"],
                      base="dcgm_cpu", marker_seen=marker_seen)]
    return legs


def test_matrix_scf_cost_is_solo_minus_variant_and_residual_ignores_variants():
    groups = {"dcgm_cpu": ["arm_aarch64_imc", "host_cpu_hw"]}
    m = collector_matrix.build_matrix(_matrix_legs(), groups, ARM_ACTIVE)
    (v,) = m["variants"]
    assert v["variant"] == "dcgm_cpu_noscf" and v["base"] == "dcgm_cpu" and v["status"] == "ok"
    assert abs(v["cpu_core_s_per_h"]["mean"] - 0.6) < 1e-9
    assert v["cpu_core_s_per_h"]["n_blocks"] == 2
    assert v["perf_event_fds_delta"] == 4  # the 4 SCF counters (2 events × 2 sockets)
    # Residual = ALL − [NONE + (SOLO − NONE)] uses driver groups only, never the variant.
    assert abs(m["residual"]["cpu_core_s_per_h"]["mean"] - (130.0 - 123.0)) < 1e-9


def test_matrix_variant_without_daemon_marker_is_not_measured_not_zero():
    """A daemon without the knob runs two identical legs: report NOT_MEASURED, never ~0."""
    legs = _matrix_legs(marker_seen=False)
    for leg in legs:
        if leg["kind"] == "VARIANT":
            leg["process"]["cpu_core_s_per_h"] = _ci(123.0)
    m = collector_matrix.build_matrix(legs, {"dcgm_cpu": ["arm_aarch64_imc", "host_cpu_hw"]},
                                      ARM_ACTIVE)
    (v,) = m["variants"]
    assert v["status"] == "NOT_MEASURED" and "marker" in v["reason"]
    assert "cpu_core_s_per_h" not in v


SCHEMA = ("$hpcperfstats 3.0\n$hostname c122-018\n"
          "!nvidia_gpu gpu_util,U=% nvlink_rx_bytes,E,U=B nvlink_tx_bytes,E,U=B\n"
          "!host_cpu_hw aperf,E mperf,E\n")


def test_schema_parse_last_message_and_diff():
    msgs = ["1 j h\nhost_cpu ...", SCHEMA, "2 j h\nnvidia_gpu 0 1 2 3"]
    assert schema_capture.last_schema_message(msgs) == SCHEMA
    assert schema_capture.last_schema_message(["1 j h\nrow"]) is None
    keys = schema_capture.parse_schema_keys(SCHEMA)
    assert keys == {"nvidia_gpu": ["gpu_util", "nvlink_rx_bytes", "nvlink_tx_bytes"],
                    "host_cpu_hw": ["aperf", "mperf"]}
    cand = {"nvidia_gpu": ["gpu_util", "nvlink_rx_bytes"], "host_cpu_hw": ["aperf", "mperf", "x"]}
    d = schema_capture.diff_keys(keys, cand)
    assert d["changed"] == {"nvidia_gpu": {"added": [], "removed": ["nvlink_tx_bytes"]},
                            "host_cpu_hw": {"added": ["x"], "removed": []}}
    merged = schema_capture.merge_leg_keys([{"a": ["1"]}, {"a": ["1", "2"], "b": ["3"]}])
    assert merged == {"a": ["1", "2"], "b": ["3"]}


def test_compare_schema_keys_cli_fails_on_removed_key(tmp_path):
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    keys = schema_capture.parse_schema_keys(SCHEMA)
    base.write_text(json.dumps({"schema_keys": {"status": "ok", "types": keys}}))
    cand.write_text(json.dumps({"schema_keys": {"status": "ok", "types": keys}}))
    cmd = [sys.executable, str(ROOT / "bin" / "compare_schema_keys.py"), str(base), str(cand),
           "--type", "nvidia_gpu"]
    ok = subprocess.run(cmd, capture_output=True, text=True)
    assert ok.returncode == 0 and "SCHEMA_KEYS_OK" in ok.stdout
    dropped = dict(keys, nvidia_gpu=["gpu_util", "nvlink_rx_bytes"])
    cand.write_text(json.dumps({"schema_keys": {"status": "ok", "types": dropped}}))
    bad = subprocess.run(cmd, capture_output=True, text=True)
    assert bad.returncode == 1 and "SCHEMA_KEYS_REMOVED" in bad.stdout
    cand.write_text(json.dumps({"schema_keys": {"status": "NOT_MEASURED", "reason": "x"}}))
    nm = subprocess.run(cmd, capture_output=True, text=True)
    assert nm.returncode == 2 and "NOT_MEASURED" in nm.stdout


class _GetHandler(http.server.BaseHTTPRequestHandler):
    calls: list = []
    reply = b"[]"

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).calls.append((self.path, json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).reply)))
        self.end_headers()
        self.wfile.write(type(self).reply)

    def log_message(self, *args):
        pass


@pytest.fixture
def get_server():
    _GetHandler.calls = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _GetHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


def test_drain_reads_only_the_measure_queue(get_server):
    _GetHandler.reply = json.dumps([{"payload": SCHEMA}, {"payload": "1 j h\nrow"}]).encode()
    conf = {"server": "127.0.0.1", "queue": "horizon", "user": "u", "password": "p"}
    res = rmq_cleanup.drain_measure_queue(conf, mgmt_port=get_server.server_address[1])
    assert res["status"] == "ok" and res["messages"][0] == SCHEMA
    path, body = _GetHandler.calls[-1]
    assert path == "/api/queues/%2F/horizon_measure/get"
    assert body["ackmode"] == "ack_requeue_false" and body["count"] > 0
    down = rmq_cleanup.drain_measure_queue(conf, mgmt_port=1, timeout=2)
    assert down["status"].startswith("management API unreachable") and down["messages"] == []


def test_variant_marker_is_recomputed_from_the_leg_journal(tmp_path):
    from lib import leg_summary

    meta = {"leg_id": "b1-VARIANT-dcgm_cpu_noscf", "kind": "VARIANT", "block": 1,
            "group": "dcgm_cpu_noscf", "base": "dcgm_cpu", "marker": MARKER,
            "env": {"HPCPERFSTATS_LIKWID_GRACE_SCF": "0"}, "phases": {}}
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    assert leg_summary.summarize_leg(tmp_path)["marker_seen"] is False  # no journal yet
    (tmp_path / "journal.txt").write_text("hpcperfstatsd[1]: host_cpu_hw: LIKWID " + MARKER
                                          + "; arm_aarch64_imc uses the perf dmc/imc scan\n")
    s = leg_summary.summarize_leg(tmp_path)
    assert s["marker_seen"] is True and s["base"] == "dcgm_cpu"
    solo = dict(meta, kind="SOLO", group="dcgm_cpu", marker=None)
    (tmp_path / "meta.json").write_text(json.dumps(solo))
    assert leg_summary.summarize_leg(tmp_path)["marker_seen"] is None


def test_report_shows_scf_variant_and_profile_records_schema_keys(tmp_path):
    from lib import profile_report

    run = tmp_path / "run"
    legs = _matrix_legs()
    for leg in legs:
        d = run / "raw" / "legs" / leg["leg_id"]
        d.mkdir(parents=True)
        (d / "summary.json").write_text(json.dumps(leg))
    (run / "raw" / "legs" / "b1-SOLO-dcgm_cpu" / "schema.txt").write_text(SCHEMA)
    (run / "raw" / "run.json").write_text(json.dumps({
        "groups": {"dcgm_cpu": ["arm_aarch64_imc", "host_cpu_hw"]}, "active_types": ARM_ACTIVE,
        "flags": {}, "schedule": {"legs": [], "seed": 1, "fit_steps": []},
        "planned_leg_ids": []}))
    (run / "raw" / "provenance.json").write_text(json.dumps({"host": {"short": "c1", "gpus": []}}))
    profile_report.write_outputs(run)
    report = (run / "report.md").read_text()
    assert "## Paired variants" in report and "| dcgm_cpu_noscf | dcgm_cpu |" in report
    assert "Grace SCF counters + arm_aarch64_imc | 0.6" in report
    prof = json.loads((run / "profile.json").read_text())
    assert prof["schema_keys"]["status"] == "ok"
    assert prof["schema_keys"]["types"]["nvidia_gpu"][1] == "nvlink_rx_bytes"
    assert prof["collector_matrix"]["variants"][0]["status"] == "ok"
    # Runs from before schema capture record NOT_MEASURED, never an empty key list.
    (run / "raw" / "legs" / "b1-SOLO-dcgm_cpu" / "schema.txt").unlink()
    assert profile_report.build_profile(run)["schema_keys"]["status"] == "NOT_MEASURED"
