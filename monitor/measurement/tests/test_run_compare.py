"""bin/compare_runs.py: per-leg parity catalog across overhead-profile runs (campaign 2026-11-09)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lib import run_compare

ROOT = Path(__file__).resolve().parents[1]
CONF = ("server stats.{site}.tacc.utexas.edu\nqueue {site}\nport 5672\nuser REDACTED\n"
        "password REDACTED\nbuffer_hours {buf}\nsample_freq 30\nsample_freq_slow 600\n"
        "send_freq 300\nenable_slow_tier 1\n")


def _run(tmp: Path, name: str, site: str, leg_seconds: int = 1800, buf: int = 6,
         fit: list | None = None, shared: bool = False, groups=("arm_cpu", "filesystem")) -> Path:
    raw = tmp / name / "raw"
    raw.mkdir(parents=True)
    legs = [{"kind": k, "leg_id": f"b1-{k}-"} for k in ("OFF", "NONE", "ALL")]
    legs += [{"kind": "SOLO", "leg_id": f"b1-SOLO-{g}"} for g in groups]
    (raw / "run.json").write_text(json.dumps({
        "schedule": {"leg_seconds": leg_seconds, "settle_seconds": 60, "fwq_seconds": 300,
                     "blocks": 3, "governors": ["performance"], "off_legs": True,
                     "fit_steps": fit or [], "groups": list(groups), "variants": [],
                     "legs": legs},
        "flags": {}}))
    (raw / "provenance.json").write_text(json.dumps({
        "host": {"short": name, "kernel": "k", "os_release": 'PRETTY_NAME="Rocky Linux 9.7"\n',
                 "nproc_online": 144, "numa_nodes": 2, "mem_total_kib": 2 ** 28, "gpus": []},
        "build": {"conf_redacted": CONF.format(site=site, buf=buf),
                  "build_capabilities": {"capability_slug": "s", "configure": {
                      "shared_node_multi_jid": shared}},
                  "git": {"commit": "dabedea0fc986cb5", "dirty": False}}}))
    return tmp / name


def test_identical_settings_compare_despite_build_site_and_collectors(tmp_path):
    runs = [run_compare.load_run(_run(tmp_path, "c122-018", "horizon", shared=True,
                                      groups=("arm_cpu", "dcgm_gpu", "filesystem"))),
            run_compare.load_run(_run(tmp_path, "i618-141", "vista"))]
    result = run_compare.compare(runs)
    assert result["ok"], [r for r in result["must"] if not r["same"]]
    intended = {r["key"]: r["values"] for r in result["intended"]}
    assert intended["build.shared_node_multi_jid"] == [True, False]
    assert intended["groups"] == ["arm_cpu,dcgm_gpu,filesystem", "arm_cpu,filesystem"]
    assert not any(r["key"] in ("conf.server", "conf.queue") for r in result["must"])


def test_campaign_20261109_differences_are_caught(tmp_path):
    runs = [run_compare.load_run(_run(tmp_path, "c122-018", "horizon", leg_seconds=1200, buf=48,
                                      fit=[{"step": "shorten_legs", "leg_seconds": 1200}])),
            run_compare.load_run(_run(tmp_path, "i618-141", "vista"))]
    result = run_compare.compare(runs)
    diff = {r["key"] for r in result["must"] if not r["same"]}
    assert not result["ok"]
    assert diff == {"schedule.leg_seconds", "conf.buffer_hours", "fit_steps"}
    md = run_compare.to_markdown(result)
    assert "NOT COMPARABLE" in md and "| schedule.leg_seconds | **DIFF** | 1200 | 1800 |" in md


def test_fit_steps_alone_fail_even_when_everything_else_matches(tmp_path):
    fit = [{"step": "drop_second_all"}]
    runs = [run_compare.load_run(_run(tmp_path, "a", "vista", fit=fit)),
            run_compare.load_run(_run(tmp_path, "b", "vista", fit=fit))]
    result = run_compare.compare(runs)
    assert not result["ok"]
    assert [r["key"] for r in result["must"] if not r["same"]] == ["fit_steps"]


def test_cli_exit_codes_and_out_file(tmp_path):
    a, b = _run(tmp_path, "a", "vista"), _run(tmp_path, "b", "vista", buf=48)
    out = tmp_path / "cmp.md"
    cli = [sys.executable, str(ROOT / "bin" / "compare_runs.py")]
    res = subprocess.run(cli + [str(a), str(b), "--out", str(out)], capture_output=True, text=True)
    assert res.returncode == 1 and "conf.buffer_hours" in out.read_text()
    res = subprocess.run(cli + [str(a), str(tmp_path)], capture_output=True, text=True)
    assert res.returncode == 2 and "not a run directory" in res.stderr


def test_parse_conf_drops_comments_and_keeps_last_value():
    conf = run_compare.parse_conf("# c\nsample_freq 60\n\nsample_freq 30\nflag\n")
    assert conf == {"sample_freq": "30", "flag": ""}
