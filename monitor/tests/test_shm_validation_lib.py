#!/usr/bin/env python3
"""Unit tests for shm validation Python libraries."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.device_validate import validate_devices_in_payload  # noqa: E402
from lib.golden_diff import (  # noqa: E402
    compare_golden_shm,
    resolve_optin_golden_dir,
)
from lib.listend_contract import (  # noqa: E402
    listend_host_from_schema,
    validate_schema_listend_contract,
)
from lib.row_validate import (  # noqa: E402
    st_name_looks_like_driver,
    token_is_uint,
    validate_metric_row,
    validate_sample_payload,
)
from lib.tacc_system_profiles import (  # noqa: E402
    STAMPEDE3,
    VISTA,
    check_profile_type_contract,
    expectations_basename,
    golden_basename,
    resolve_system,
    stampede3_profiles,
    vista_profiles,
)
from lib.value_plausibility import check_plausibility  # noqa: E402

# Import emitter helpers for fleet slug tests
import emit_build_capabilities as ebc  # noqa: E402


SYNTHETIC = MONITOR / "tests" / "expected" / "synthetic_fixture"
CROSS_SAMPLE = MONITOR / "tests" / "expected" / "cross_sample_fixture"


def _load_manifest() -> dict:
    return json.loads(
        (SYNTHETIC / "expectations_synthetic_debug_tier1.json").read_text(encoding="utf-8")
    )


def _schema_by_type(manifest: dict) -> dict[str, list[str]]:
    return {k: v["schema_keys"] for k, v in manifest["types"].items()}


class RowValidateTests(unittest.TestCase):
    def test_driver_name_and_uint(self) -> None:
        self.assertTrue(st_name_looks_like_driver("host_net"))
        self.assertFalse(st_name_looks_like_driver("Host_Net"))
        self.assertTrue(token_is_uint("42"))
        self.assertFalse(token_is_uint("-1"))
        self.assertFalse(token_is_uint("3.14"))

    def test_bad_uint_fails(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        body = (SYNTHETIC / "full").read_text(encoding="utf-8")
        bad = body.replace("host_net eth0 @full 10 11 12", "host_net eth0 @full 10 x 12")
        with self.assertRaises(ValueError) as ctx:
            validate_sample_payload(bad, schema, require_tier=True, allowed_tier="@full")
        self.assertIn("non-numeric", str(ctx.exception))

    def test_synthetic_fixture_passes(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        n = validate_sample_payload(full, schema, require_tier=True, allowed_tier="@full")
        self.assertGreater(n, 0)


class DeviceValidateTests(unittest.TestCase):
    def test_wrong_dev_fails(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        bad = full.replace("host_net eth0", "host_net eth99")
        with self.assertRaises(ValueError) as ctx:
            validate_devices_in_payload(
                bad, manifest, schema, require_tier=True, allowed_tier="@full"
            )
        self.assertIn("eth99", str(ctx.exception))

    def test_host_mem_numa_nodes(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        manifest = json.loads(json.dumps(manifest))
        manifest["types"]["host_mem"]["devices"] = ["0", "1"]
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        numa_full = full.replace("host_mem mem @full 20 11 12", "host_mem 0 @full 20 11 12")
        numa_full += "\nhost_mem 1 @full 20 11 12"
        validate_devices_in_payload(
            numa_full, manifest, schema, require_tier=True, allowed_tier="@full"
        )
        bad = numa_full.replace("host_mem 1 @full", "host_mem 9 @full")
        with self.assertRaises(ValueError) as ctx:
            validate_devices_in_payload(
                bad, manifest, schema, require_tier=True, allowed_tier="@full"
            )
        self.assertIn("9", str(ctx.exception))


class HostLiveProbeTests(unittest.TestCase):
    def test_host_mem_default_devices_are_numa_nodes(self) -> None:
        from unittest.mock import patch

        from lib.host_live_probes import default_devices_for_type

        with patch("lib.host_live_probes.probe_numa_node_devices", return_value=["0", "1"]):
            self.assertEqual(default_devices_for_type("host_mem"), ["0", "1"])
            self.assertEqual(default_devices_for_type("host_numa"), ["0", "1"])
        self.assertEqual(default_devices_for_type("host_ps"), ["-"])

    def test_probe_ib_skips_hfi_probe_opa_slash(self) -> None:
        import tempfile
        from unittest.mock import patch

        from lib.host_live_probes import probe_ib_devices, probe_opa_devices

        with tempfile.TemporaryDirectory() as tmp:
            ib = Path(tmp)
            hfi = ib / "hfi1_0"
            p1 = hfi / "ports" / "1"
            p1.mkdir(parents=True)
            (p1 / "state").write_text("4: ACTIVE\n", encoding="utf-8")
            (p1 / "phys_state").write_text("5: LinkUp\n", encoding="utf-8")
            mlx = ib / "mlx5_0"
            (mlx / "ports" / "1").mkdir(parents=True)

            real_path = Path

            def fake_path(arg: str) -> Path:
                if arg == "/sys/class/infiniband":
                    return ib
                return real_path(arg)

            with patch("lib.host_live_probes.Path", side_effect=fake_path):
                self.assertEqual(probe_ib_devices(), ["mlx5_0.1"])
                self.assertEqual(probe_opa_devices(), ["hfi1_0/1"])

    def test_probe_opa_skips_down_cn5000_port(self) -> None:
        """CN5000 dual-port: DOWN port1 must not appear; ACTIVE port2 must (opa.c)."""
        import tempfile
        from unittest.mock import patch

        from lib.host_live_probes import probe_opa_devices

        with tempfile.TemporaryDirectory() as tmp:
            ib = Path(tmp)
            hfi = ib / "hfi1_0"
            p1 = hfi / "ports" / "1"
            p2 = hfi / "ports" / "2"
            p1.mkdir(parents=True)
            p2.mkdir(parents=True)
            (p1 / "state").write_text("1: DOWN\n", encoding="utf-8")
            (p1 / "phys_state").write_text("9: <unknown>\n", encoding="utf-8")
            (p2 / "state").write_text("4: ACTIVE\n", encoding="utf-8")
            (p2 / "phys_state").write_text("5: LinkUp\n", encoding="utf-8")

            real_path = Path

            def fake_path(arg: str) -> Path:
                if arg == "/sys/class/infiniband":
                    return ib
                return real_path(arg)

            with patch("lib.host_live_probes.Path", side_effect=fake_path):
                self.assertEqual(probe_opa_devices(), ["hfi1_0/2"])

    def test_probe_net_includes_up_lo(self) -> None:
        import tempfile
        from unittest.mock import patch

        from lib.host_live_probes import probe_net_devices

        with tempfile.TemporaryDirectory() as tmp:
            net = Path(tmp)
            lo = net / "lo"
            lo.mkdir()
            (lo / "flags").write_text("0x9\n")
            down = net / "eth0"
            down.mkdir()
            (down / "flags").write_text("0x0\n")

            real_path = Path

            def fake_path(arg: str) -> Path:
                if arg == "/sys/class/net":
                    return net
                return real_path(arg)

            with patch("lib.host_live_probes.Path", side_effect=fake_path):
                self.assertEqual(probe_net_devices(), ["lo"])

    def test_probe_nfs_mount_paths(self) -> None:
        from unittest.mock import patch

        from lib.host_live_probes import probe_nfs_mount_devices

        mountstats = (
            "device nfs-server:/export mounted on /home1 with fstype nfs statvers=1.0\n"
            "device nfs-server:/export mounted on /home2 with fstype nfs statvers=1.1\n"
            "device local:/srv mounted on /local with fstype nfs4 statvers=1.0\n"
        )
        with patch("lib.host_live_probes._read_lines", return_value=mountstats.splitlines()):
            self.assertEqual(probe_nfs_mount_devices(), ["/home1", "/home2"])

    def test_merge_device_lists_observed_over_singleton(self) -> None:
        from lib.host_live_probes import merge_device_lists

        self.assertEqual(
            merge_device_lists(["-"], ["/home1"]),
            ["/home1"],
        )
        self.assertEqual(
            merge_device_lists(["eno1"], ["lo"]),
            ["eno1", "lo"],
        )

    def test_observed_devices_from_shm_fixture(self) -> None:
        from lib.payload_parse import observed_devices_from_shm

        observed = observed_devices_from_shm(SYNTHETIC, enable_slow_tier=True)
        self.assertIn("host_net", observed)
        self.assertEqual(observed["host_net"], ["eth0"])

    def _install_edac_dimm(self, root: Path, mc: str, dimm: str, speed: str, mem_type: str) -> None:
        dimm_dir = root / mc / dimm
        dimm_dir.mkdir(parents=True, exist_ok=True)
        (dimm_dir / "dimm_mem_speed").write_text(speed, encoding="utf-8")
        (dimm_dir / "dimm_mem_type").write_text(mem_type, encoding="utf-8")

    def test_probe_edac_mem_classes_ddr_only(self) -> None:
        import os
        import tempfile

        from lib.host_live_probes import probe_edac_mem_classes, probe_spr_imc_devices

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._install_edac_dimm(root, "mc0", "dimm0", "4800\n", "DDR5\n")
            os.environ["HPCPERFSTATS_EDAC_MC_ROOT"] = str(root)
            try:
                has_ddr, has_hbm = probe_edac_mem_classes()
                self.assertTrue(has_ddr)
                self.assertFalse(has_hbm)
                devs = probe_spr_imc_devices(has_ddr, has_hbm)
                self.assertEqual(len(devs), 16)
                self.assertEqual(devs[0], "mbox0")
                self.assertNotIn("hbm0", devs)
            finally:
                os.environ.pop("HPCPERFSTATS_EDAC_MC_ROOT", None)

    def test_probe_edac_mem_classes_hbm_only(self) -> None:
        import os
        import tempfile

        from lib.host_live_probes import probe_edac_mem_classes, probe_spr_imc_devices

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._install_edac_dimm(root, "mc0", "dimm0", "6400\n", "HBM2e\n")
            os.environ["HPCPERFSTATS_EDAC_MC_ROOT"] = str(root)
            try:
                has_ddr, has_hbm = probe_edac_mem_classes()
                self.assertFalse(has_ddr)
                self.assertTrue(has_hbm)
                devs = probe_spr_imc_devices(has_ddr, has_hbm)
                self.assertEqual(len(devs), 16)
                self.assertEqual(devs[0], "hbm0")
                self.assertNotIn("mbox0", devs)
            finally:
                os.environ.pop("HPCPERFSTATS_EDAC_MC_ROOT", None)

    def test_probe_edac_mem_classes_mixed(self) -> None:
        import os
        import tempfile

        from lib.host_live_probes import probe_edac_mem_classes, probe_spr_imc_devices

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._install_edac_dimm(root, "mc0", "dimm0", "4800\n", "DDR5\n")
            self._install_edac_dimm(root, "mc0", "dimm1", "6400\n", "HBM3\n")
            os.environ["HPCPERFSTATS_EDAC_MC_ROOT"] = str(root)
            try:
                has_ddr, has_hbm = probe_edac_mem_classes()
                self.assertTrue(has_ddr)
                self.assertTrue(has_hbm)
                devs = probe_spr_imc_devices(has_ddr, has_hbm)
                self.assertEqual(len(devs), 32)
            finally:
                os.environ.pop("HPCPERFSTATS_EDAC_MC_ROOT", None)

    def test_default_devices_spr_imc_hbm_only(self) -> None:
        from lib.host_live_probes import default_devices_for_type

        with patch("lib.host_live_probes.probe_spr_imc_devices", return_value=["hbm0", "hbm1"]):
            self.assertEqual(default_devices_for_type("intel_x86_uncore_imc_spr"), ["hbm0", "hbm1"])

    def test_spr_imc_device_validate_hbm_only_manifest(self) -> None:
        schema_keys = [
            " dram_cas_reads,E,W=48",
            " dram_cas_writes,E,W=48",
            " hbm_cas_reads,E,W=48",
            " hbm_cas_writes,E,W=48",
        ]
        manifest = {
            "types": {
                "intel_x86_uncore_imc_spr": {
                    "schema_keys": schema_keys,
                    "devices": [f"hbm{i}" for i in range(16)],
                }
            }
        }
        schema = {"intel_x86_uncore_imc_spr": schema_keys}
        body = (
            "1700000000.0 job0 host\n"
            "intel_x86_uncore_imc_spr hbm3 @full 1 2 3 4\n"
        )
        validate_devices_in_payload(
            body, manifest, schema, require_tier=True, allowed_tier="@full"
        )
        bad = body.replace("hbm3", "mbox0")
        with self.assertRaises(ValueError) as ctx:
            validate_devices_in_payload(
                bad, manifest, schema, require_tier=True, allowed_tier="@full"
            )
        self.assertIn("mbox0", str(ctx.exception))


class ListendContractTests(unittest.TestCase):
    def test_schema_host_token(self) -> None:
        schema = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        self.assertEqual(listend_host_from_schema(schema), "golden_host")

    def test_wrong_schema_host_fails(self) -> None:
        manifest = _load_manifest()
        schema = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        bad = schema.replace("$hostname golden_host", "$hostname wrong_host")
        with self.assertRaises(ValueError) as ctx:
            validate_schema_listend_contract(bad, manifest)
        self.assertIn("wrong_host", str(ctx.exception))


class PlausibilityTests(unittest.TestCase):
    def test_mem_relation_warns(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        bad = full.replace("host_mem mem @full 20 11 12", "host_mem mem @full 5 11 12")
        _, warnings, _ = check_plausibility(
            manifest,
            schema,
            full_body=bad,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=False,
        )
        self.assertTrue(any("mem_total < mem_free" in w for w in warnings))

    def test_strict_promotes_to_error(self) -> None:
        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        bad = full.replace("host_mem mem @full 20 11 12", "host_mem mem @full 5 11 12")
        _, _, errors = check_plausibility(
            manifest,
            schema,
            full_body=bad,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=True,
        )
        self.assertTrue(any("mem_total < mem_free" in e for e in errors))

    def test_host_tmpfs_missing_silent_when_not_tmpfs(self) -> None:
        """Schema lists host_tmpfs but /tmp is not tmpfs → no missing-type WARN."""
        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_tmpfs": {
                    "schema_keys": ["bytes_used,E,U=B", "bytes_avail,E,U=B"],
                    "devices": [],
                }
            },
        }
        schema = {"host_tmpfs": manifest["types"]["host_tmpfs"]["schema_keys"]}
        # Header-only sample: no metric rows (host_tmpfs absent from payload).
        body = "1234567890.0 0 golden_host\n"
        _, warnings, _ = check_plausibility(
            manifest,
            schema,
            full_body=body,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=False,
        )
        self.assertFalse(any("host_tmpfs" in w and "missing" in w for w in warnings))

    def test_host_tmpfs_missing_warns_when_expected(self) -> None:
        """Manifest devices show /tmp tmpfs → missing row WARNs."""
        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_tmpfs": {
                    "schema_keys": ["bytes_used,E,U=B", "bytes_avail,E,U=B"],
                    "devices": ["/tmp"],
                }
            },
        }
        schema = {"host_tmpfs": manifest["types"]["host_tmpfs"]["schema_keys"]}
        body = "1234567890.0 0 golden_host\n"
        _, warnings, _ = check_plausibility(
            manifest,
            schema,
            full_body=body,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=False,
        )
        self.assertTrue(any("host_tmpfs" in w and "missing" in w for w in warnings))


class CrossSampleTests(unittest.TestCase):
    def test_schema_token_is_event_counter(self) -> None:
        from lib.message_parse import schema_token_is_event_counter

        self.assertTrue(schema_token_is_event_counter("rx_bytes,E,U=B"))
        self.assertFalse(schema_token_is_event_counter("mem_total,U=KB"))

    def test_wait_bounds_debug_rpm(self) -> None:
        from lib.daemon_conf import DaemonTiming, wait_bounds_from_timing

        t = DaemonTiming(
            sample_freq=30.0,
            sample_freq_slow=60.0,
            send_freq=300.0,
            enable_slow_tier=True,
        )
        b = wait_bounds_from_timing(t)
        self.assertEqual(b.fast_timeout_sec, 75.0)
        self.assertEqual(b.full_timeout_sec, 102.0)
        self.assertEqual(b.fast_cadence_max, 75.0)
        self.assertEqual(b.full_cadence_max, 90.0)

    def test_parse_daemon_conf_debug_values(self) -> None:
        from lib.daemon_conf import parse_daemon_conf

        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "hpcperfstats.conf"
            conf.write_text(
                "sample_freq 30\nsample_freq_slow 60\nenable_slow_tier 1\n",
                encoding="utf-8",
            )
            t = parse_daemon_conf(conf)
            self.assertEqual(t.sample_freq, 30.0)
            self.assertEqual(t.sample_freq_slow, 60.0)
            self.assertTrue(t.enable_slow_tier)

    def test_discover_explicit_conf(self) -> None:
        from lib.daemon_conf import discover_active_conf

        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "site.conf"
            conf.write_text("sample_freq 45\nsample_freq_slow 90\n", encoding="utf-8")
            active = discover_active_conf(explicit_conf=conf)
            self.assertEqual(active.timing.sample_freq, 45.0)
            self.assertIn("explicit", active.source)

    def test_fixture_pair_passes(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import load_fixture_timing
        from lib.shm_snapshot import load_fixture_pair

        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        timing = load_fixture_timing(CROSS_SAMPLE / "fixture_timing.json")
        fast_pair = load_fixture_pair(CROSS_SAMPLE, "fast")
        full_pair = load_fixture_pair(CROSS_SAMPLE, "full")
        notes, warnings, errors = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=fast_pair,
            full_pair=full_pair,
            strict=False,
            active_conf_note="test fixture",
        )
        self.assertFalse(errors)
        self.assertTrue(any("fast_ts" in n for n in notes))
        self.assertTrue(any("full_ts" in n for n in notes))

    def test_monotonic_regression_warns(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = _load_manifest()
        schema = _schema_by_type(manifest)
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a=(CROSS_SAMPLE / "t1" / "full").read_text(encoding="utf-8"),
            body_b=(CROSS_SAMPLE / "t0" / "full").read_text(encoding="utf-8"),
        )
        _, warnings, errors = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
        )
        self.assertTrue(any("decreased" in w for w in warnings))

    def test_wait_for_timestamp_advance(self) -> None:
        from lib.shm_snapshot import wait_for_timestamp_advance

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fast"
            path.write_text(
                "1000.0 job host\nhost_net eth0 @fast 1 2 3\n",
                encoding="utf-8",
            )

            def advance():
                path.write_text(
                    "1030.0 job host\nhost_net eth0 @fast 2 3 4\n",
                    encoding="utf-8",
                )

            with patch("lib.shm_snapshot.time.sleep", side_effect=lambda _: advance()):
                ts, body = wait_for_timestamp_advance(
                    path, 1000.0, timeout_sec=5.0, poll_interval_sec=0.01
                )
            self.assertEqual(ts, 1030.0)
            self.assertIn("1030.0", body)


class GoldenDiffTests(unittest.TestCase):
    def test_golden_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            golden_dir = td / "goldens"
            golden_dir.mkdir()
            slug = "synthetic_debug_tier1"
            for name in ("schema", "fast", "full"):
                text = (SYNTHETIC / name).read_text(encoding="utf-8")
                (td / name).write_text(text, encoding="utf-8")
                (golden_dir / f"shm_{name}_{slug}.txt").write_text(text, encoding="utf-8")
            errs, compared = compare_golden_shm(td, golden_dir, slug, enable_slow_tier=True)
            self.assertEqual(errs, [])
            self.assertEqual(compared, 3)

    def test_golden_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            slug = "synthetic_debug_tier1"
            (td / "full").write_text("123 1 host\nhost_ps - 1 2 3 4\n", encoding="utf-8")
            golden = td / f"shm_full_{slug}.txt"
            golden.write_text("different\n", encoding="utf-8")
            errs, compared = compare_golden_shm(td, td, slug, enable_slow_tier=False)
            self.assertEqual(compared, 1)
            self.assertTrue(any("differs" in e for e in errs))

    def test_golden_zero_compared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            (td / "full").write_text("123 1 host\n", encoding="utf-8")
            errs, compared = compare_golden_shm(
                td, td, "missing_slug", enable_slow_tier=False
            )
            self.assertEqual(errs, [])
            self.assertEqual(compared, 0)

    def test_resolve_optin_not_requested(self) -> None:
        self.assertIsNone(
            resolve_optin_golden_dir(
                monitor_dir=MONITOR,
                slug="x",
                golden_dir_env=None,
                golden_check=False,
                enable_slow_tier=True,
            )
        )

    def test_resolve_optin_auto_finds_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mon = Path(tmp)
            expected = mon / "tests" / "expected"
            expected.mkdir(parents=True)
            slug = "live_slug"
            (expected / f"shm_full_{slug}.txt").write_text("x\n", encoding="utf-8")
            path = resolve_optin_golden_dir(
                monitor_dir=mon,
                slug=slug,
                golden_dir_env="auto",
                golden_check=False,
                enable_slow_tier=False,
            )
            self.assertEqual(path, expected.resolve())

    def test_resolve_optin_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mon = Path(tmp)
            (mon / "tests" / "expected").mkdir(parents=True)
            self.assertIsNone(
                resolve_optin_golden_dir(
                    monitor_dir=mon,
                    slug="absent",
                    golden_dir_env="auto",
                    golden_check=True,
                    enable_slow_tier=True,
                )
            )

    def test_golden_with_profile_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            golden_dir = td / "goldens"
            golden_dir.mkdir()
            slug = "fleet_slug"
            profile = "h100"
            text = "123 1 host\nnvidia_gpu 0 @full 1 2 3\n"
            (td / "full").write_text(text, encoding="utf-8")
            (golden_dir / golden_basename("full", slug, profile)).write_text(
                text, encoding="utf-8"
            )
            errs, compared = compare_golden_shm(
                td, golden_dir, slug, enable_slow_tier=False, profile=profile
            )
            self.assertEqual(errs, [])
            self.assertEqual(compared, 1)


class TaccSystemProfilesTests(unittest.TestCase):
    def test_fixture_dirs_complete(self) -> None:
        self.assertEqual(
            stampede3_profiles(),
            frozenset({"skx", "icx", "spr", "h100", "pvc", "amd-rtx"}),
        )
        self.assertEqual(vista_profiles(), frozenset({"gg", "gh"}))

    def test_resolve_system(self) -> None:
        self.assertEqual(resolve_system("h100"), STAMPEDE3)
        self.assertEqual(resolve_system("gg"), VISTA)
        self.assertEqual(resolve_system("gh", "vista"), VISTA)
        with self.assertRaises(ValueError):
            resolve_system("nope")

    def test_artifact_names(self) -> None:
        self.assertEqual(
            expectations_basename("slug", "h100"), "expectations_slug__h100.json"
        )
        self.assertEqual(expectations_basename("slug"), "expectations_slug.json")
        self.assertEqual(
            golden_basename("schema", "slug", "skx"), "shm_schema_slug__skx.txt"
        )

    def test_stampede3_contract_h100(self) -> None:
        ok = {"nvidia_gpu", "host_ib", "host_opa", "cpu"}
        self.assertEqual(check_profile_type_contract(ok, "h100"), [])
        bad = check_profile_type_contract({"host_opa"}, "h100")
        self.assertTrue(any("missing" in e for e in bad))
        forbid = check_profile_type_contract(ok | {"amd_gpu"}, "amd-rtx")
        self.assertTrue(any("forbidden" in e for e in forbid))

    def test_vista_provisional_contracts(self) -> None:
        self.assertEqual(
            check_profile_type_contract({"host_ib", "cpu"}, "gg"), []
        )
        self.assertEqual(
            check_profile_type_contract(
                {"nvidia_gpu", "host_ib", "host_cpu_hw"}, "gh"
            ),
            [],
        )
        miss = check_profile_type_contract({"cpu"}, "gh")
        self.assertTrue(any("missing" in e for e in miss))
        miss_hw = check_profile_type_contract({"nvidia_gpu", "host_ib"}, "gh")
        self.assertTrue(any("host_cpu_hw" in e for e in miss_hw))
        opa = check_profile_type_contract({"host_ib", "host_opa"}, "gg")
        self.assertTrue(any("forbidden" in e for e in opa))

    def test_relax_skips_contract(self) -> None:
        self.assertEqual(
            check_profile_type_contract(set(), "h100", relax=True), []
        )


class PapiShmValidateTests(unittest.TestCase):
    def _papi_manifest(self, *, keys: list[str] | None = None, hybrid: bool = True) -> dict:
        required = [
            "aperf",
            "mperf",
            "cpu_clock_est_cycles",
            "fp_arith_inst_retired_scalar_single",
            "fp_arith_inst_retired_scalar_double",
            "arm_est_flops",
            "arm_int8_ops",
            "arm_int16_ops",
        ]
        names = keys if keys is not None else required
        doc: dict = {
            "capability_slug": "test",
            "enable_slow_tier": True,
            "types": {
                "host_cpu_hw": {
                    "schema_keys": [f"{k},E" for k in names],
                    "schema_key_names": names,
                    "devices": ["0"],
                }
            },
        }
        if hybrid:
            doc["compile_capabilities"] = {
                "configure": {"papi_hybrid": True, "cpu_backend": "dcgm"}
            }
        return doc

    def test_schema_contract_pass(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        notes, errors = check_papi_schema_contract(self._papi_manifest())
        self.assertEqual(errors, [])
        self.assertTrue(any("PASS papi schema" in n for n in notes))

    def test_schema_contract_missing_int_keys(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        keys = [
            "aperf",
            "mperf",
            "cpu_clock_est_cycles",
            "fp_arith_inst_retired_scalar_single",
            "fp_arith_inst_retired_scalar_double",
            "arm_est_flops",
        ]
        _, errors = check_papi_schema_contract(self._papi_manifest(keys=keys))
        self.assertTrue(any("arm_int8_ops" in e for e in errors))
        self.assertTrue(any("arm_int16_ops" in e for e in errors))

    def test_hybrid_requires_host_cpu_hw(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        man = {
            "types": {},
            "compile_capabilities": {"configure": {"papi_hybrid": True}},
        }
        _, errors = check_papi_schema_contract(man)
        self.assertTrue(any("missing type" in e for e in errors))

    def test_flops_invariant_warn(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        warns = check_papi_row_invariants(
            {
                "fp_arith_inst_retired_scalar_single": 10,
                "fp_arith_inst_retired_scalar_double": 20,
                "arm_est_flops": 99,
            },
            type_name="host_cpu_hw",
            dev="0",
        )
        self.assertTrue(any("arm_est_flops" in w for w in warns))

    def test_flops_invariant_ok(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        warns = check_papi_row_invariants(
            {
                "fp_arith_inst_retired_scalar_single": 10,
                "fp_arith_inst_retired_scalar_double": 20,
                "arm_est_flops": 30,
                "arm_int8_ops": 1,
                "arm_int16_ops": 2,
                "aperf": 5,
                "mperf": 5,
                "cpu_clock_est_cycles": 5,
            },
            type_name="host_cpu_hw",
            dev="0",
        )
        self.assertEqual(warns, [])

    def test_host_cpu_hw_devices_probe(self) -> None:
        from lib.host_live_probes import default_devices_for_type

        with patch(
            "lib.host_live_probes.probe_cpu_devices", return_value=["0", "1", "2"]
        ):
            self.assertEqual(default_devices_for_type("host_cpu_hw"), ["0", "1", "2"])

    def test_plausibility_flags_papi_flops_mismatch(self) -> None:
        man = self._papi_manifest()
        schema = {"host_cpu_hw": man["types"]["host_cpu_hw"]["schema_keys"]}
        vals = ["5", "5", "5", "10", "20", "99", "1", "2"]
        body = (
            "1234567890.0 0 golden_host\n"
            f"host_cpu_hw 0 @full {' '.join(vals)}\n"
        )
        _, warnings, _ = check_plausibility(
            man,
            schema,
            full_body=body,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=False,
        )
        self.assertTrue(any("arm_est_flops" in w for w in warnings))


class EmitBuildCapabilitiesFleetTests(unittest.TestCase):
    def test_slug_tokens_dyn_intel(self) -> None:
        features = {"HARDWARE", "INFINIBAND", "OPA", "INTEL_GPU", "GPU"}
        slug = ebc.build_capability_slug(
            arch="x86_64",
            version="1.0",
            debug=True,
            features=features,
            cpu_backend="likwid",
            tier="slowtier1",
            ib_mad_dlopen=True,
            opa_mad_dlopen=True,
        )
        self.assertIn("ibdyn", slug)
        self.assertIn("opadyn", slug)
        self.assertIn("intelgpu", slug)
        self.assertIn("-ib-", f"-{slug}-")
        self.assertIn("-opa-", f"-{slug}-")
        self.assertIn("-nvgpu-", f"-{slug}-")

    def test_slug_omits_dyn_when_absent(self) -> None:
        slug = ebc.build_capability_slug(
            arch="x86_64",
            version="1.0",
            debug=False,
            features={"INFINIBAND"},
            cpu_backend=None,
            tier="slowtier0",
            ib_mad_dlopen=False,
            opa_mad_dlopen=False,
        )
        self.assertNotIn("ibdyn", slug)
        self.assertNotIn("opadyn", slug)
        self.assertNotIn("intelgpu", slug)

    def test_fleet_stampede3_signature(self) -> None:
        self.assertEqual(
            ebc._fleet_stampede3(
                ib_mad_dlopen=True,
                opa_mad_dlopen=True,
                features={"INTEL_GPU"},
            ),
            "stampede3",
        )
        self.assertIsNone(
            ebc._fleet_stampede3(
                ib_mad_dlopen=True,
                opa_mad_dlopen=True,
                features={"INTEL_GPU", "AMD_GPU"},
            )
        )

    def test_parse_makefile_dyn_macros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DMONITOR_WITH_INFINIBAND -DMONITOR_WITH_OPA "
                "-DMONITOR_WITH_INTEL_GPU -DMONITOR_IB_MAD_DLOPEN "
                "-DMONITOR_OPA_MAD_DLOPEN -DMONITOR_CPU_BACKEND_LIKWID -DDEBUG\n",
                encoding="utf-8",
            )
            features, cpu, debug, ibdyn, opadyn, papi = ebc._parse_src_makefile(td)
            self.assertIn("INTEL_GPU", features)
            self.assertTrue(ibdyn)
            self.assertTrue(opadyn)
            self.assertTrue(debug)
            self.assertEqual(cpu, "likwid")
            self.assertFalse(papi)

    def test_parse_makefile_papi_hybrid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DMONITOR_CPU_BACKEND_DCGM -DMONITOR_CPU_PAPI_FLOPS\n",
                encoding="utf-8",
            )
            _f, cpu, _d, _i, _o, papi = ebc._parse_src_makefile(td)
            self.assertEqual(cpu, "dcgm")
            self.assertTrue(papi)


if __name__ == "__main__":
    unittest.main()
