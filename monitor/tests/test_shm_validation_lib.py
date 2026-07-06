#!/usr/bin/env python3
"""Unit tests for shm validation Python libraries."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.device_validate import validate_devices_in_payload  # noqa: E402
from lib.golden_diff import compare_golden_shm  # noqa: E402
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
from lib.value_plausibility import check_plausibility  # noqa: E402

SYNTHETIC = MONITOR / "tests" / "expected" / "synthetic_fixture"


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
            errs = compare_golden_shm(td, golden_dir, slug, enable_slow_tier=True)
            self.assertEqual(errs, [])

    def test_golden_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            slug = "synthetic_debug_tier1"
            (td / "full").write_text("123 1 host\nhost_ps - 1 2 3 4\n", encoding="utf-8")
            golden = td / f"shm_full_{slug}.txt"
            golden.write_text("different\n", encoding="utf-8")
            errs = compare_golden_shm(td, td, slug, enable_slow_tier=False)
            self.assertTrue(any("differs" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
