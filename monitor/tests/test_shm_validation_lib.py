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
    listend_capability_slug_from_schema,
    listend_host_from_schema,
    validate_schema_listend_contract,
)
from lib.message_parse import schema_key_name  # noqa: E402
from lib.row_validate import (  # noqa: E402
    st_name_looks_like_driver,
    token_is_uint,
    token_is_value,
    token_sentinel_code,
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
        self.assertIn("neither unsigned nor a catalog sentinel", str(ctx.exception))

    def test_token_is_value_accepts_catalog_sentinels_only(self) -> None:
        for n in range(1, 12):
            self.assertTrue(token_is_value(f"-{n}"), n)
            self.assertEqual(token_sentinel_code(f"-{n}"), n)
        self.assertTrue(token_is_value("0"))
        self.assertTrue(token_is_value("18446744073709551615"))
        for bad in ("-99", "-12", "-0", "-03", "-", "", "+3", "-3x", "3.0"):
            self.assertFalse(token_is_value(bad), bad)
        self.assertIsNone(token_sentinel_code("7"))

    def test_metric_row_sentinel_accepted_out_of_catalog_rejected(self) -> None:
        schema = _schema_by_type(_load_manifest())
        validate_metric_row(
            "host_net eth0 @full 10 -3 -10", schema, require_tier=True, allowed_tier="@full"
        )
        with self.assertRaises(ValueError):
            validate_metric_row(
                "host_net eth0 @full 10 -99 12", schema, require_tier=True, allowed_tier="@full"
            )

    def test_value_accessors_split_values_from_sentinels(self) -> None:
        from lib.payload_parse import metric_sentinel_at_key, metric_value_at_key

        names = ["rx_bytes", "tx_bytes", "collisions"]
        values = ["10", "-3", "x"]
        self.assertEqual(metric_value_at_key(values, names, "rx_bytes"), 10)
        self.assertIsNone(metric_value_at_key(values, names, "tx_bytes"))
        self.assertEqual(metric_sentinel_at_key(values, names, "tx_bytes"), 3)
        self.assertIsNone(metric_sentinel_at_key(values, names, "rx_bytes"))
        self.assertIsNone(metric_value_at_key(values, names, "collisions"))
        self.assertIsNone(metric_value_at_key(values, names, "absent"))


class SentinelCatalogTests(unittest.TestCase):
    def test_catalog_matches_c_header(self) -> None:
        import re

        from lib.sentinel_catalog import CATALOG_VERSION, SENTINEL_SLUGS

        text = (MONITOR / "src" / "stats_sentinel.h").read_text(encoding="utf-8")
        entries = re.findall(r"^\s*X\((\d+), (\w+),", text, re.M)
        self.assertEqual({int(n): slug for n, slug in entries}, SENTINEL_SLUGS)
        self.assertEqual(sorted(SENTINEL_SLUGS), list(range(1, len(SENTINEL_SLUGS) + 1)))
        self.assertIn(f"STATS_SENTINEL_CATALOG_VERSION {CATALOG_VERSION}", text)

    def test_bridgeable_codes_match_consumer_catalog(self) -> None:
        import re

        from lib.sentinel_catalog import BRIDGEABLE_CODES

        self.assertEqual(BRIDGEABLE_CODES, frozenset({1, 3, 7, 8, 9}))
        consumer = MONITOR.parent / "hpcperfstats" / "lib" / "monitor_sentinel.py"
        if not consumer.is_file():
            self.skipTest("consumer catalog not in this checkout")
        rows = re.findall(
            r'SentinelCode\(\s*-(\d+),\s*"\w+",\s*"[^"]*",\s*(True|False)',
            consumer.read_text(encoding="utf-8"),
        )
        self.assertTrue(rows)
        self.assertEqual(
            frozenset(int(n) for n, bridge in rows if bridge == "True"), BRIDGEABLE_CODES
        )

    def test_banner_version(self) -> None:
        from lib.sentinel_catalog import sentinels_banner_version

        self.assertEqual(sentinels_banner_version("$build x\n$sentinels 1\n!t a"), 1)
        self.assertIsNone(sentinels_banner_version("$build x\n!t a"))
        self.assertIsNone(sentinels_banner_version(None))


class SentinelLayerTests(unittest.TestCase):
    def _layer(self, schema_body: str, full_body: str):
        from lib.sentinel_validate import check_sentinel_banner

        schema = _schema_by_type(_load_manifest())
        return check_sentinel_banner(schema_body, {"full": (full_body, True, "@full")}, schema)

    def test_knob0_capture_reports_skip_not_pass(self) -> None:
        schema_body = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        full = (SYNTHETIC / "full").read_text(encoding="utf-8")
        notes, warnings, errors, banner = self._layer(schema_body, full)
        self.assertIsNone(banner)
        self.assertFalse(errors or warnings)
        self.assertTrue(any(n.startswith("SKIP sentinel layers") for n in notes), notes)
        self.assertFalse(any(n.startswith("PASS sentinel") for n in notes), notes)

    def test_sentinel_token_without_banner_fails(self) -> None:
        schema_body = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        full = (SYNTHETIC / "full").read_text(encoding="utf-8").replace(
            "host_net eth0 @full 10 11 12", "host_net eth0 @full 10 -3 12"
        )
        _, _, errors, _ = self._layer(schema_body, full)
        self.assertTrue(any("without a $sentinels banner" in e for e in errors), errors)

    def test_banner_counts_per_code(self) -> None:
        schema_body = (SYNTHETIC / "schema").read_text(encoding="utf-8").replace(
            "$uptime 1000\n", "$uptime 1000\n$sentinels 1\n"
        )
        full = (SYNTHETIC / "full").read_text(encoding="utf-8").replace(
            "host_net eth0 @full 10 11 12", "host_net eth0 @full -10 -3 -3"
        )
        notes, _, errors, banner = self._layer(schema_body, full)
        self.assertEqual(banner, 1)
        self.assertFalse(errors)
        self.assertIn("PASS sentinel banner v1", notes)
        self.assertIn("NOTE sentinel full: NOT_BUILT=1 READ_FAILED=2", notes)

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
            mp1 = mlx / "ports" / "1"
            mp1.mkdir(parents=True)
            (mp1 / "state").write_text("4: ACTIVE\n", encoding="utf-8")
            (mp1 / "phys_state").write_text("5: LinkUp\n", encoding="utf-8")
            (mp1 / "link_layer").write_text("InfiniBand\n", encoding="utf-8")

            real_path = Path

            def fake_path(arg: str) -> Path:
                if arg == "/sys/class/infiniband":
                    return ib
                return real_path(arg)

            with patch("lib.host_live_probes.Path", side_effect=fake_path):
                self.assertEqual(probe_ib_devices(), ["mlx5_0.1"])
                self.assertEqual(probe_opa_devices(), ["hfi1_0/1"])

    def test_probe_ib_skips_active_ethernet_horizon_shape(self) -> None:
        """Horizon-style: ACTIVE Ethernet mlx HCA must not appear; InfiniBand must."""
        import tempfile
        from unittest.mock import patch

        from lib.host_live_probes import probe_ib_devices

        with tempfile.TemporaryDirectory() as tmp:
            ib = Path(tmp)
            for name, link in (("mlx5_0", "InfiniBand"), ("mlx5_2", "Ethernet")):
                p = ib / name / "ports" / "1"
                p.mkdir(parents=True)
                (p / "state").write_text("4: ACTIVE\n", encoding="utf-8")
                (p / "phys_state").write_text("5: LinkUp\n", encoding="utf-8")
                (p / "link_layer").write_text(f"{link}\n", encoding="utf-8")

            real_path = Path

            def fake_path(arg: str) -> Path:
                if arg == "/sys/class/infiniband":
                    return ib
                return real_path(arg)

            with patch("lib.host_live_probes.Path", side_effect=fake_path):
                self.assertEqual(probe_ib_devices(), ["mlx5_0.1"])

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

    def test_probe_power_socket_matches_hwmon_meters(self) -> None:
        import tempfile

        from lib.host_live_probes import probe_power_socket_devices

        with tempfile.TemporaryDirectory() as tmp:
            hw = Path(tmp) / "class" / "hwmon"
            meters = [
                ("hwmon9", "", "CPU Power Socket 0"),
                ("hwmon3", "", "Grace Power Socket 1"),
                ("hwmon4", "device", "SysIO Power Socket 1"),
                ("hwmon5", "", "Vera Power Socket 3"),
            ]
            for name, sub, label in meters:
                d = hw / name / sub if sub else hw / name
                d.mkdir(parents=True)
                (d / "power1_oem_info").write_text(label + "\n")
                (d / "power1_average").write_text("45000000\n")
            nolabel = hw / "hwmon0"
            nolabel.mkdir()
            (nolabel / "name").write_text("coretemp\n")
            self.assertEqual(probe_power_socket_devices(tmp), ["0", "1"])
            self.assertEqual(probe_power_socket_devices(str(Path(tmp) / "missing")), [])

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
            "device odd:/x mounted on /odd with fstype nfs3x statvers=1.1\n"
            "device nfsd mounted on /proc/fs/nfsd with fstype nfsd\n"
        )
        with patch("lib.host_live_probes._read_lines", return_value=mountstats.splitlines()):
            self.assertEqual(probe_nfs_mount_devices(), ["/home1", "/home2", "/local"])

    def test_probe_beegfs_mount_paths(self) -> None:
        from unittest.mock import patch

        from lib.host_live_probes import probe_beegfs_mount_devices

        mounts = (
            "beegfs_nodev /scratch beegfs rw,relatime,cfgFile=/etc/beegfs/beegfs-client.conf 0 0\n"
            "beegfs_nodev /data beegfs_nodev rw,relatime 0 0\n"
            "/dev/sda1 / boot ext4 rw,relatime 0 0\n"
        )
        with patch("lib.host_live_probes.Path") as mock_path:
            inst = mock_path.return_value
            inst.is_file.return_value = True
            inst.read_text.return_value = mounts
            self.assertEqual(probe_beegfs_mount_devices(), ["/data", "/scratch"])

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

    def _uncore_devices(self, names: list[str], type_name: str) -> list[str] | None:
        import os

        from lib.host_live_probes import default_devices_for_type

        with tempfile.TemporaryDirectory() as tmp:
            for name in names:
                (Path(tmp) / name).mkdir()
            with patch.dict(os.environ, {"HPCPERFSTATS_PERF_DEVICES_DIR": tmp}):
                return default_devices_for_type(type_name)

    def test_spr_uncore_rows_follow_sysfs_pmus(self) -> None:
        # Regression: the probe hard-coded mbox0..15 / hbm0..15 from EDAC, while x86_uncore_perf.c
        # emits one row per uncore_imc_<N> / uncore_hbm_<N> PMU (an SPR HBM part has 32 HBM PMUs).
        names = [f"uncore_imc_{i}" for i in (0, 2, 10)] + [f"uncore_hbm_{i}" for i in range(32)]
        names += ["uncore_imc_free_running_0", "uncore_hbmx", "cpu", "power"]
        devs = self._uncore_devices(names, "intel_x86_uncore_imc_spr")
        self.assertEqual(devs[:3], ["mbox0", "mbox2", "mbox10"])
        self.assertEqual(devs[3:], [f"hbm{i}" for i in range(32)])
        self.assertEqual(self._uncore_devices(names, "intel_x86_uncore_imc_emr"), devs)

    def test_uncore_rows_per_class_and_df(self) -> None:
        names = ["uncore_imc_1", "uncore_imc_0", "uncore_hbm_0", "amd_df"]
        self.assertEqual(self._uncore_devices(names, "intel_x86_uncore_imc_gnr"), ["mbox0", "mbox1"])
        self.assertEqual(self._uncore_devices(names, "intel_x86_uncore_imc_icx"), ["mdev0", "mdev1"])
        self.assertEqual(self._uncore_devices(names, "amd_x86_uncore_df_turin"), ["df"])
        self.assertIsNone(self._uncore_devices(["cpu"], "intel_x86_uncore_imc_skx"))
        self.assertIsNone(self._uncore_devices(["cpu"], "amd_x86_uncore_df_milan"))

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

    def test_missing_build_ok(self) -> None:
        """Synthetic fixture has no $build; validators must tolerate absence."""
        manifest = _load_manifest()
        schema = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        self.assertIsNone(listend_capability_slug_from_schema(schema))
        validate_schema_listend_contract(schema, manifest)

    def test_build_slug_mismatch_fails(self) -> None:
        manifest = _load_manifest()
        schema = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        with_build = schema.replace(
            "$uptime 1000\n",
            "$uptime 1000\n$build wrong-slug\n",
        )
        man = dict(manifest)
        man["capability_slug"] = "expected-slug"
        with self.assertRaises(ValueError) as ctx:
            validate_schema_listend_contract(with_build, man)
        self.assertIn("$build", str(ctx.exception))

    def test_build_slug_match_ok(self) -> None:
        manifest = _load_manifest()
        schema = (SYNTHETIC / "schema").read_text(encoding="utf-8")
        slug = manifest.get("capability_slug") or "synthetic_debug_tier1"
        with_build = schema.replace(
            "$uptime 1000\n",
            f"$uptime 1000\n$build {slug}\n",
        )
        man = dict(manifest)
        man["capability_slug"] = slug
        self.assertEqual(listend_capability_slug_from_schema(with_build), slug)
        validate_schema_listend_contract(with_build, man)
        # Host still second $ line
        self.assertEqual(listend_host_from_schema(with_build), "golden_host")


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

    def test_stale_sample_age_strict_is_error(self) -> None:
        """Age >300s under strict must be an error (SEGV restart-loop / stale shm class)."""
        manifest = {"enable_slow_tier": True, "types": {}}
        schema: dict = {}
        # Epoch far in the past relative to now.
        body = "1000000000.0 0 golden_host\n"
        _, warnings, errors = check_plausibility(
            manifest,
            schema,
            full_body=body,
            fast_body=None,
            schema_body=None,
            no_freshness=False,
            strict=True,
        )
        self.assertEqual(warnings, [])
        self.assertTrue(any("sample timestamp age" in e and "> 300s" in e for e in errors))

    def test_intel_gpu_mem_total_without_power_warns(self) -> None:
        """PVC-style intel_gpu row: mem_total populated but power=0 → plausibility WARN."""
        schema_keys = [
            "gpu_util",
            "gpu_mem_util",
            "gpu_mem_total_mb,U=MB",
            "gpu_mem_used_mb,U=MB",
            "power_usage,U=W",
            "temperature,U=C",
            "gpu_sm_clock",
            "sm_active",
            "gpu_dram_active",
            "gpu_pcie_rx_bytes,E,W=64,U=B",
            "gpu_pcie_tx_bytes,E,W=64,U=B",
            "gpu_xe_link_rx_bytes,E,W=64,U=B",
            "gpu_xe_link_tx_bytes,E,W=64,U=B",
            "clocks_event_reasons",
            "gpu_count",
        ]
        manifest = {
            "enable_slow_tier": True,
            "types": {
                "intel_gpu": {
                    "schema_keys": schema_keys,
                    "schema_key_names": [k.split(",")[0] for k in schema_keys],
                    "devices": ["0"],
                }
            },
        }
        schema = {"intel_gpu": schema_keys}
        vals = ["0", "0", "131072", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "4"]
        body = "1234567890.0 0 golden_host\n" f"intel_gpu 0 @full {' '.join(vals)}\n"
        _, warnings, _ = check_plausibility(
            manifest,
            schema,
            full_body=body,
            fast_body=None,
            schema_body=None,
            no_freshness=True,
            strict=False,
        )
        self.assertTrue(any("power_usage=0" in w for w in warnings))


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

    def _w48_pair(self, va: int, vb: int, wide_a: int, wide_b: int):
        """host_cpu_hw row with one W=48 key (aperf) and one W=64 key (cycles)."""
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_cpu_hw": {
                    "schema_keys": ["aperf,E,W=48", "cpu_clock_est_cycles,E,W=64"],
                    "schema_key_names": ["aperf", "cpu_clock_est_cycles"],
                }
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a=f"1000.0 job h\nhost_cpu_hw 0 @full {va} {wide_a}\n",
            body_b=f"1060.0 job h\nhost_cpu_hw 0 @full {vb} {wide_b}\n",
        )
        return manifest, schema, pair

    def _run_strict_full(self, manifest, schema, pair):
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming

        return run_cross_sample_checks(
            manifest,
            schema,
            timing=DaemonTiming(30.0, 60.0, 300.0, True),
            fast_pair=None,
            full_pair=pair,
            strict=True,
            active_conf_note="test",
        )

    def test_w48_wrap_is_an_advance_not_a_decrease(self) -> None:
        """3.1 emits Grace aperf modulo 2^48; a wrap (~26 h per core) must not fail strict L5."""
        top = (1 << 48) - 1000
        wide = (1 << 48) - 1000
        manifest, schema, pair = self._w48_pair(top, 500, wide, wide + 1500)
        notes, warnings, errors = self._run_strict_full(manifest, schema, pair)
        self.assertEqual([e for e in errors if "decreased" in e], [], errors)
        self.assertFalse(any("decreased" in w for w in warnings))
        self.assertTrue(any("monotonic full (2 E keys)" in n for n in notes), notes)

    def test_w48_real_drop_still_fails(self) -> None:
        """A drop larger than half the 48-bit range is a restart, not a wrap."""
        manifest, schema, pair = self._w48_pair(10**12, 10**9, 5, 6)
        _, _, errors = self._run_strict_full(manifest, schema, pair)
        self.assertTrue(any("aperf: decreased" in e for e in errors), errors)

    def test_w64_drop_is_never_a_wrap(self) -> None:
        manifest, schema, pair = self._w48_pair(5, 6, (1 << 63), 7)
        _, _, errors = self._run_strict_full(manifest, schema, pair)
        self.assertTrue(any("cpu_clock_est_cycles: decreased" in e for e in errors), errors)

    def test_flat_counters_are_a_note_not_a_warning(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_net": {
                    "schema_keys": [
                        "rx_bytes,E,U=B",
                        "rx_errors,E",
                        "tx_errors,E",
                    ],
                    "schema_key_names": ["rx_bytes", "rx_errors", "tx_errors"],
                }
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a="1000.0 job h\nhost_net eth0 @full 100 0 0\n",
            body_b="1060.0 job h\nhost_net eth0 @full 100 0 0\n",
        )
        notes, warnings, errors = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
        )
        self.assertFalse(errors)
        self.assertEqual([w for w in warnings if "flat" in w], [])
        self.assertTrue(any("expected when idle): host_net 3 keys" in n for n in notes), notes)

    def test_host_cpu_hw_all_zero_aggregated(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_cpu_hw": {
                    "schema_keys": ["cycles,E", "instr,E", "arm_est_flops,E"],
                    "schema_key_names": ["cycles", "instr", "arm_est_flops"],
                }
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a="1000.0 job h\nhost_cpu_hw 0 @full 0 0 0\n",
            body_b="1060.0 job h\nhost_cpu_hw 0 @full 0 0 0\n",
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
        self.assertFalse(errors)
        self.assertEqual(len([w for w in warnings if "host_cpu_hw" in w]), 1)
        self.assertTrue(any("all E-keys flat at 0" in w for w in warnings))
        self.assertFalse(any("cycles: flat" in w for w in warnings))

    def _pair_result(self, types: dict, body_a: str, body_b: str) -> tuple[list, list]:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {"enable_slow_tier": True, "types": {
            t: {"schema_keys": k, "schema_key_names": [x.split(",")[0] for x in k]}
            for t, k in types.items()}}
        pair = SnapshotPair(kind="full", ts_a=1000.0, ts_b=1060.0,
                            body_a="1000.0 - h\n" + body_a, body_b="1060.0 - h\n" + body_b)
        notes, warnings, errors = run_cross_sample_checks(
            manifest, types, timing=DaemonTiming(30.0, 60.0, 300.0, True), fast_pair=None,
            full_pair=pair, strict=False, active_conf_note="test")
        self.assertFalse(errors)
        return notes, warnings

    def test_idle_node_flats_pass_and_decreases_fail(self) -> None:
        """c122-018 2026-11-09: idle CPU 99, idle GPU, unused NUMA node, idle disk -> NOTE only."""
        keys = {"host_cpu_hw": ["cpu_util_total_accum_us,E", "cpu_util_user_accum_us,E",
                                "instr_retired,E", "mperf,E"],
                "nvidia_gpu": ["gpu_util", "gpu_nvlink_tx_bytes,E,W=64,U=B", "gpu_flops,E,W=64"],
                "host_numa": ["numa_hit,E", "numa_miss,E"],
                "host_block": ["rd_ios,E", "wr_merges,E"]}
        a = ("host_cpu_hw 99 @full 58769178 58769178 177508607436 54937145200\n"
             "nvidia_gpu 0 @full 0 863393 0\nhost_numa 8 @full 0 0\nhost_block dm-1 @full 42 0\n")
        b = a.replace("58769178 58769178", "58779296 58769178")
        notes, warnings = self._pair_result(keys, a, b)
        self.assertEqual(warnings, [])
        note = next(n for n in notes if "expected when idle" in n)
        for part in ("host_block 2 keys", "host_cpu_hw 3 keys", "host_numa 2 keys",
                     "nvidia_gpu 2 keys"):
            self.assertIn(part, note)
        _, warnings = self._pair_result(keys, a, a.replace("nvidia_gpu 0 @full 0 863393",
                                                           "nvidia_gpu 0 @full 0 175"))
        self.assertEqual(warnings, ["WARN cross_sample nvidia_gpu/0 gpu_nvlink_tx_bytes: "
                                    "decreased 863393 -> 175 (possible restart?)"])

    def test_host_cpu_flat_is_note(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_cpu": {
                    "schema_keys": ["user,E,U=cs", "system,E,U=cs", "idle,E,U=cs"],
                    "schema_key_names": ["user", "system", "idle"],
                }
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a="1000.0 job h\nhost_cpu 0 @full 10 20 30\n",
            body_b="1060.0 job h\nhost_cpu 0 @full 10 20 30\n",
        )
        notes, warnings, _ = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
        )
        self.assertFalse(any("flat" in w for w in warnings))
        self.assertTrue(any("host_cpu 3 keys" in n for n in notes), notes)

    def test_canary_fail_and_pass(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_cpu": {
                    "schema_keys": ["user,E,U=cs", "idle,E,U=cs"],
                    "schema_key_names": ["user", "idle"],
                },
                "host_vm": {
                    "schema_keys": ["pgfault,E"],
                    "schema_key_names": ["pgfault"],
                },
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        stuck = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a="1000.0 job h\nhost_cpu 0 @full 1 2\nhost_vm - @full 5\n",
            body_b="1060.0 job h\nhost_cpu 0 @full 1 2\nhost_vm - @full 5\n",
        )
        notes, warnings, _ = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=stuck,
            strict=False,
            active_conf_note="test",
            check_canaries=True,
        )
        self.assertTrue(any("canary host_cpu" in w for w in warnings))
        self.assertTrue(any("canary host_vm/host_block" in w for w in warnings))

        moving = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1060.0,
            body_a="1000.0 job h\nhost_cpu 0 @full 1 2\nhost_vm - @full 5\n",
            body_b="1060.0 job h\nhost_cpu 0 @full 5 2\nhost_vm - @full 9\n",
        )
        notes, warnings, _ = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=moving,
            strict=False,
            active_conf_note="test",
            check_canaries=True,
        )
        self.assertFalse(any("canary" in w for w in warnings))
        self.assertTrue(any("canary host_cpu" in n for n in notes))
        self.assertTrue(any("canary host_vm/host_block" in n for n in notes))

    def test_cadence_boundary_epsilon(self) -> None:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {
            "enable_slow_tier": True,
            "types": {
                "host_net": {
                    "schema_keys": ["rx_bytes,E,U=B"],
                    "schema_key_names": ["rx_bytes"],
                }
            },
        }
        schema = {k: v["schema_keys"] for k, v in manifest["types"].items()}
        timing = DaemonTiming(30.0, 60.0, 300.0, True)
        # full_cadence_max = 1.5 * 60 = 90; delta slightly over due to float noise
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1000.0 + 90.0 + 5e-7,
            body_a="1000.0 job h\nhost_net eth0 @full 1\n",
            body_b="1090.0 job h\nhost_net eth0 @full 2\n",
        )
        notes, warnings, errors = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
        )
        self.assertFalse(errors)
        self.assertFalse(any("outside" in w for w in warnings))
        self.assertTrue(any("full_ts" in n for n in notes))

        over = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1000.0 + 90.0 + 0.01,
            body_a="1000.0 job h\nhost_net eth0 @full 1\n",
            body_b="1090.01 job h\nhost_net eth0 @full 2\n",
        )
        _, warnings, _ = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=over,
            strict=False,
            active_conf_note="test",
        )
        self.assertTrue(any("outside" in w for w in warnings))

    def test_stimulus_context_cleans_up(self) -> None:
        from lib.cross_sample_stimulus import cross_sample_stimulus

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch(
                "lib.cross_sample_stimulus._probe_lustre_mount", return_value=None
            ):
                with cross_sample_stimulus(
                    n_cpu_workers=1,
                    tmp_dir=tmp_path,
                    io_bytes=64 * 1024,
                    io_interval_sec=0.05,
                    enable_lustre=False,
                ) as stim:
                    self.assertFalse(stim.touched_lustre)
                    # Let IO loop run once
                    import time

                    time.sleep(0.12)
            leftovers = list(tmp_path.glob("hpcperfstats_cross_sample_stim_*"))
            self.assertEqual(leftovers, [])

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


PSOCK_KEYS = ["cpu_power_w,U=W", "socket_power_w,U=W", "sysio_power_w,U=W", "module_power_w,U=W"]
GPU_KEYS = ["gpu_util", "gpu_mem_total_mb,U=MB", "gpu_mem_used_mb,U=MB", "gpu_mem_free_mb,U=MB"]


def _round2_manifest(schema: dict[str, list[str]], *, gpu_built: bool = True) -> dict:
    return {
        "enable_slow_tier": True,
        "host_fqdn": "",
        "compile_capabilities": {"configure": {"gpu": gpu_built}},
        "types": {t: {"schema_keys": k, "schema_key_names": [x.split(",")[0] for x in k]}
                  for t, k in schema.items()},
    }


class Round2ShmChecksTests(unittest.TestCase):
    """host_power_socket / nvidia_gpu NVML / retired-key checks added for round 2."""

    def test_probe_power_socket_watts_reads_meters(self) -> None:
        from lib.host_live_probes import probe_power_socket_watts

        with tempfile.TemporaryDirectory() as tmp:
            hw = Path(tmp) / "class" / "hwmon"
            for name, sub, label, uw in (
                ("hwmon9", "", "CPU Power Socket 0", "44200000"),
                ("hwmon2", "device", "Grace Power Socket 0", "88000000"),
                ("hwmon3", "", "SysIO Power Socket 1", "garbage"),
                ("hwmon4", "", "Vera Power Socket 1", "1000000"),
            ):
                d = hw / name / sub if sub else hw / name
                d.mkdir(parents=True)
                (d / "power1_oem_info").write_text(label + "\n")
                (d / "power1_average").write_text(uw + "\n")
            self.assertEqual(
                probe_power_socket_watts(tmp),
                {("0", "cpu_power_w"): 44.2, ("0", "socket_power_w"): 88.0},
            )
            self.assertEqual(probe_power_socket_watts(str(Path(tmp) / "none")), {})

    def test_schema_contract_flags_retired_and_wrong_keys(self) -> None:
        from lib.value_plausibility import check_schema_contract

        bad = check_schema_contract({
            "host_cpu_hw": ["cpu_util_total_accum_us,E,W=64", "dcgm_cpu_power_util_w,U=W"],
            "nvidia_gpu": ["gpu_util", "sysio_power_usage,U=W"],
            "host_power_socket": ["cpu_power_w,U=W", "socket_power_w,U=W"],
        })
        self.assertEqual(len(bad), 3, bad)
        self.assertTrue(any("dcgm_cpu_power_util_w" in m for m in bad))
        self.assertTrue(any("sysio_power_usage" in m for m in bad))
        self.assertEqual(check_schema_contract({"host_power_socket": PSOCK_KEYS,
                                                "nvidia_gpu": GPU_KEYS}), [])

    def test_power_socket_row_invariants(self) -> None:
        from lib.value_plausibility import check_row_invariants

        def run(cpu, sock, sysio, mod):
            return check_row_invariants("host_power_socket", "0", {
                "cpu_power_w": cpu, "socket_power_w": sock, "sysio_power_w": sysio,
                "module_power_w": mod})

        self.assertEqual(run(44, 88, 6, 0), [])  # GB200: no Module meter
        self.assertEqual(run(44, 46, 1, 130), [])  # GH200 shape
        self.assertTrue(any("cpu_power_w=95 > socket_power_w=88" in m for m in run(95, 88, 6, 0)))
        self.assertTrue(any("every meter reads 0 W" in m for m in run(0, 0, 0, 0)))
        self.assertTrue(any("> 2000" in m for m in run(44, 88, 6, 5000)))

    def test_nvidia_gpu_row_invariants(self) -> None:
        from lib.value_plausibility import check_row_invariants

        ok = {"sm_active": 95, "gpu_util": 100, "gpu_mem_total_mb": 189471,
              "gpu_mem_used_mb": 600, "gpu_mem_free_mb": 188300, "temperature": 34}
        self.assertEqual(check_row_invariants("nvidia_gpu", "0", ok), [])
        bad = check_row_invariants("nvidia_gpu", "0", {**ok, "sm_active": 9545,
                                                       "gpu_mem_used_mb": 9000, "temperature": 300})
        self.assertEqual(len(bad), 3, bad)

    def test_nvidia_gpu_pipe_split_keys_capped_at_100(self) -> None:
        from lib.value_plausibility import check_row_invariants

        ok = {"integer_active": 100, "tensor_dmma_active": 37}
        self.assertEqual(check_row_invariants("nvidia_gpu", "0", ok), [])
        bad = check_row_invariants("nvidia_gpu", "0", {"integer_active": 101,
                                                       "tensor_dmma_active": 5000})
        self.assertEqual(sorted(bad), ["nvidia_gpu/0: integer_active=101 > 100 %",
                                       "nvidia_gpu/0: tensor_dmma_active=5000 > 100 %"])

    def test_nvidia_gpu_percent_keys_cover_every_pipe_duty_cycle(self) -> None:
        """Every `*_active` key nvidia_gpu.h emits is a 0-100 duty cycle."""
        import re

        from lib.value_plausibility import NVIDIA_GPU_PERCENT_KEYS

        header = (MONITOR / "src" / "nvidia_gpu.h").read_text(encoding="utf-8")
        active = set(re.findall(r"\bX\((\w+_active),", header))
        self.assertIn("integer_active", active)
        self.assertIn("tensor_dmma_active", active)
        self.assertEqual(active - set(NVIDIA_GPU_PERCENT_KEYS), set())

    def test_intel_gpu_compute_engine_util_capped_at_100(self) -> None:
        from lib.value_plausibility import check_row_invariants

        self.assertEqual(check_row_invariants("intel_gpu", "0", {"compute_engine_util": 100}), [])
        self.assertEqual(check_row_invariants("intel_gpu", "0", {}), [])
        self.assertEqual(check_row_invariants("intel_gpu", "3", {"compute_engine_util": 101}),
                         ["intel_gpu/3: compute_engine_util=101 > 100 %"])

    def test_check_plausibility_flags_intel_gpu_compute_engine_util(self) -> None:
        schema = {"intel_gpu": ["gpu_util", "power_usage,U=W", "compute_engine_util"]}
        body = "1234567890.0 0 h\nintel_gpu 0 @full 50 400 250\n"
        _, warnings, errors = check_plausibility(
            _round2_manifest(schema), schema, full_body=body, fast_body=None,
            schema_body=None, no_freshness=True, strict=False)
        self.assertEqual(errors, [])
        self.assertTrue(any("compute_engine_util=250 > 100 %" in w for w in warnings), warnings)

    def test_check_plausibility_wires_round2_checks(self) -> None:
        schema = {"host_power_socket": PSOCK_KEYS, "host_cpu_hw": ["dcgm_cpu_power_util_w,U=W"]}
        body = ("1234567890.0 0 h\nhost_power_socket 0 @full 95 88 6 0\n"
                "host_cpu_hw 0 @full 0\n")
        _, warnings, errors = check_plausibility(
            _round2_manifest(schema), schema, full_body=body, fast_body=None,
            schema_body=None, no_freshness=True, strict=True)
        self.assertEqual(warnings, [])
        self.assertTrue(any("cpu_power_w=95" in e for e in errors), errors)
        self.assertTrue(any("retired key 'dcgm_cpu_power_util_w'" in e for e in errors), errors)

    def _live(self, schema, body, *, sockets=("0",), watts=None, gpus=(), smi=None,
              gpu_built=True):
        import time as _time

        from lib import live_spot_check as lsc

        watts = watts if watts is not None else {
            ("0", "cpu_power_w"): 44.0, ("0", "socket_power_w"): 88.0,
            ("0", "sysio_power_w"): 6.0}
        body = body.replace("NOW", f"{_time.time():.1f}")
        with patch.object(lsc, "probe_power_socket_devices", return_value=list(sockets)), \
                patch.object(lsc, "probe_power_socket_watts", return_value=watts), \
                patch.object(lsc, "probe_nvidia_gpu_devices", return_value=list(gpus)), \
                patch.object(lsc, "probe_nvidia_smi_mem_total_mib", return_value=smi or {}), \
                patch.object(lsc, "probe_cpu_devices", return_value=[]), \
                patch.object(lsc, "probe_loadavg_scaled", return_value={}), \
                patch.object(lsc, "probe_host_fqdn", return_value=""):
            _, warnings, _ = lsc.run_live_spot_checks(
                _round2_manifest(schema, gpu_built=gpu_built), schema, full_body=body,
                max_age_sec=300, strict=False)
        return warnings

    def test_live_power_socket_matches_hwmon(self) -> None:
        schema = {"host_power_socket": PSOCK_KEYS}
        good = "NOW - h\nhost_power_socket 0 @full 44 88 6 0\n"
        self.assertEqual(self._live(schema, good), [])
        off = self._live(schema, "NOW - h\nhost_power_socket 0 @full 90 88 6 0\n")
        self.assertTrue(any("cpu_power_w: emitted=90 W live=44.0 W" in w for w in off), off)
        ghost = self._live(schema, "NOW - h\nhost_power_socket 0 @full 44 88 6 40\n")
        self.assertTrue(any("module_power_w=40 W but no live meter" in w for w in ghost), ghost)
        short = self._live(schema, good, sockets=("0", "1"))
        self.assertTrue(any("rows ['0'] != live devices ['0', '1']" in w for w in short), short)
        missing = self._live({}, "NOW - h\n")
        self.assertTrue(any("host_power_socket missing from schema" in w for w in missing))

    def test_live_power_value_mismatch_is_warning_even_strict(self) -> None:
        """c122-018 2026-11-14: a host_power_socket sample (up to sample_freq_slow old) vs
        the live meter read while the validator itself runs (ondemand governor wakes the
        socket: emitted 10 W, live 36 W) must not fail strict runs; structural problems
        (a value with no live meter) still do."""
        import time as _time

        from lib import live_spot_check as lsc

        schema = {"host_power_socket": PSOCK_KEYS}
        watts = {("0", "cpu_power_w"): 44.0, ("0", "socket_power_w"): 88.0,
                 ("0", "sysio_power_w"): 6.0}

        def strict_run(body):
            body = body.replace("NOW", f"{_time.time():.1f}")
            with patch.object(lsc, "probe_power_socket_devices", return_value=["0"]), \
                    patch.object(lsc, "probe_power_socket_watts", return_value=watts), \
                    patch.object(lsc, "probe_nvidia_gpu_devices", return_value=[]), \
                    patch.object(lsc, "probe_nvidia_smi_mem_total_mib", return_value={}), \
                    patch.object(lsc, "probe_cpu_devices", return_value=[]), \
                    patch.object(lsc, "probe_loadavg_scaled", return_value={}), \
                    patch.object(lsc, "probe_host_fqdn", return_value=""):
                return lsc.run_live_spot_checks(
                    _round2_manifest(schema), schema, full_body=body, max_age_sec=300,
                    strict=True)

        _, warnings, errors = strict_run("NOW - h\nhost_power_socket 0 @full 10 55 6 0\n")
        self.assertEqual(errors, [])
        self.assertTrue(any("cpu_power_w: emitted=10 W live=44.0 W" in w for w in warnings),
                        warnings)
        _, _, errors = strict_run("NOW - h\nhost_power_socket 0 @full 44 88 6 40\n")
        self.assertTrue(any("module_power_w=40 W but no live meter" in e for e in errors),
                        errors)

    def test_live_required_types_skip_per_job_full_payload(self) -> None:
        """Shared node with jobs: `full` holds a jid payload, node-wide rows live in `-`."""
        self.assertEqual(self._live({}, "NOW job42 h\n", gpus=("0",)), [])
        self.assertTrue(self._live({}, "NOW - h\n", gpus=("0",)))

    def test_conditional_types_absent_without_mount_or_job(self) -> None:
        """c122-018 2026-11-09: no Lustre/BeeGFS mount, no job -> not 'missing from full payload'."""
        cond = ["beegfs_client", "lustre_llite", "lustre_mdc", "lustre_osc", "host_mem_job",
                "host_proc", "host_nfs", "host_tmpfs"]
        schema = {t: ["x"] for t in cond}
        schema["host_vm"] = ["x"]
        manifest = _round2_manifest(schema)
        body = "1234567890.0 - h\nhost_vm - @full 1\n"

        def warns(m):
            return check_plausibility(m, schema, full_body=body, fast_body=None,
                                      schema_body=None, no_freshness=True, strict=False)[1]

        self.assertEqual(warns(manifest), [])
        manifest["types"]["lustre_osc"]["devices"] = ["/scratch"]
        self.assertEqual(warns(manifest), ["WARN plausibility type 'lustre_osc' missing from full payload"])
        manifest["types"]["lustre_osc"].pop("devices")
        no_vm = "1234567890.0 - h\n"
        got = check_plausibility(manifest, schema, full_body=no_vm, fast_body=None,
                                 schema_body=None, no_freshness=True, strict=False)[1]
        self.assertEqual(got, ["WARN plausibility type 'host_vm' missing from full payload"])

    def test_live_nvidia_gpu_rows_and_mem_total(self) -> None:
        schema = {"nvidia_gpu": GPU_KEYS}
        body = "NOW - h\nnvidia_gpu 0 @full 0 189471 600 188300\n"
        self.assertEqual(self._live(schema, body, sockets=(), gpus=("0",),
                                    smi={"0": 189471}), [])
        wrong = self._live(schema, body, sockets=(), gpus=("0",), smi={"0": 97871})
        self.assertTrue(any("gpu_mem_total_mb: emitted=189471 nvidia-smi=97871" in w
                            for w in wrong), wrong)
        two = self._live(schema, body, sockets=(), gpus=("0", "1"), smi={"0": 189471})
        self.assertTrue(any("nvidia_gpu rows ['0'] != live devices" in w for w in two), two)
        absent = self._live({}, "NOW - h\n", sockets=(), gpus=("0",))
        self.assertTrue(any("nvidia_gpu missing from schema" in w for w in absent), absent)
        self.assertEqual(self._live({}, "NOW - h\n", sockets=(), gpus=("0",),
                                    gpu_built=False), [])


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
            frozenset({"skx", "clx", "icx", "spr", "h100", "pvc", "amd-rtx"}),
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

    def test_stampede3_contract_amd_rtx(self) -> None:
        ok = {
            "nvidia_gpu",
            "host_ib",
            "host_opa",
            "host_cpu_hw",
            "amd_x86_uncore_df_turin",
        }
        self.assertEqual(check_profile_type_contract(ok, "amd-rtx"), [])
        miss = check_profile_type_contract(
            {"nvidia_gpu", "host_ib", "host_opa"}, "amd-rtx"
        )
        self.assertTrue(any("host_cpu_hw" in e for e in miss))
        self.assertTrue(any("amd_x86_uncore_df_turin" in e for e in miss))

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
            "fp_ops_retired",
            "amd_fp_dp_ops",
            "pmu_enabled_time",
            "pmu_running_time",
            "pmu_solo_running_time",
        ]
        if hybrid:
            required += ["arm_fp_dp_spec", "arm_fp_sp_spec", "arm_fp_hp_spec"]
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
            doc["compile_capabilities"] = {"configure": {"cpu_backend": "arm"}}
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
            "compile_capabilities": {"configure": {"cpu_backend": "arm"}},
        }
        _, errors = check_papi_schema_contract(man)
        self.assertTrue(any("missing type" in e for e in errors))

    def test_arm_backend_requires_measured_fp_keys(self) -> None:
        """3.1 Grace (D1): an arm-backend schema without arm_fp_*_spec fails."""
        from lib.papi_shm_validate import check_papi_schema_contract

        man = self._papi_manifest()
        names = [k for k in man["types"]["host_cpu_hw"]["schema_key_names"] if k != "arm_fp_dp_spec"]
        man["types"]["host_cpu_hw"]["schema_key_names"] = names
        _, errors = check_papi_schema_contract(man)
        self.assertTrue(any("arm_fp_dp_spec" in e for e in errors))

    def test_every_backend_requires_sharing_and_flop_keys(self) -> None:
        """D6 sharing times and the x86 FLOP lifetimes are in every host_cpu_hw schema (arm and
        x86 direct perf); dropping one fails either way."""
        from lib.papi_shm_validate import check_papi_schema_contract

        for hybrid in (True, False):
            for missing in ("pmu_enabled_time", "pmu_running_time", "pmu_solo_running_time",
                            "fp_ops_retired", "amd_fp_dp_ops"):
                man = self._papi_manifest(hybrid=hybrid)
                names = [k for k in man["types"]["host_cpu_hw"]["schema_key_names"] if k != missing]
                man["types"]["host_cpu_hw"]["schema_key_names"] = names
                _, errors = check_papi_schema_contract(man)
                self.assertTrue(any(missing in e for e in errors), (hybrid, missing))

    def test_pmu_running_above_enabled_warns(self) -> None:
        """D6: running or solo time above enabled time is impossible; equal is fine."""
        from lib.papi_shm_validate import check_papi_row_invariants

        ok = check_papi_row_invariants(
            {"pmu_enabled_time": 9000, "pmu_running_time": 9000, "pmu_solo_running_time": 7000},
            type_name="host_cpu_hw",
            dev="0",
            arm=False,
        )
        self.assertFalse(any("pmu_" in w for w in ok))
        bad = check_papi_row_invariants(
            {"pmu_enabled_time": 9000, "pmu_running_time": 9001, "pmu_solo_running_time": 9500},
            type_name="host_cpu_hw",
            dev="0",
            arm=False,
        )
        self.assertTrue(any("pmu_running_time=9001" in w for w in bad))
        self.assertTrue(any("pmu_solo_running_time=9500" in w for w in bad))

    def test_x86_schema_does_not_need_arm_fp_keys(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        man = self._papi_manifest(hybrid=False)
        notes, errors = check_papi_schema_contract(man)
        self.assertEqual(errors, [])
        self.assertTrue(any("PASS papi schema" in n for n in notes))

    def test_only_cpu_backend_arm_means_arm(self) -> None:
        """The pre-3.1 overlay alias flags no longer select the arm key set."""
        from lib.papi_shm_validate import caps_indicate_arm_backend

        self.assertTrue(caps_indicate_arm_backend({"configure": {"cpu_backend": "arm"}}))
        self.assertTrue(caps_indicate_arm_backend({"compile_capabilities": {"configure": {"cpu_backend": "arm"}}}))
        self.assertFalse(caps_indicate_arm_backend({"configure": {"papi_hybrid": True}}))
        self.assertFalse(caps_indicate_arm_backend({"configure": {"cpu_backend": "none"}}))
        self.assertFalse(caps_indicate_arm_backend(None))

    def test_flops_not_checked_against_scalar_keys(self) -> None:
        """arm_est_flops is measured FIXED+SCALE FLOPs since 3.1, not scalar SP+DP (O15/D1)."""
        from lib.papi_shm_validate import check_papi_row_invariants

        warns = check_papi_row_invariants(
            {
                "fp_arith_inst_retired_scalar_single": 0,
                "fp_arith_inst_retired_scalar_double": 0,
                "arm_est_flops": 99,
            },
            type_name="host_cpu_hw",
            dev="0",
            arm=True,
        )
        self.assertFalse(any("arm_est_flops" in w for w in warns))

    def test_negative_measured_fp_key_warns(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        warns = check_papi_row_invariants(
            {"arm_fp_dp_spec": -1}, type_name="host_cpu_hw", dev="0", arm=True
        )
        self.assertTrue(any("arm_fp_dp_spec" in w for w in warns))

    def test_aperf_compared_to_cycles_modulo_2_48(self) -> None:
        """aperf is W=48, cpu_clock_est_cycles W=64: equal mod 2^48 is consistent (3.1 Grace)."""
        from lib.papi_shm_validate import check_papi_row_invariants

        cycles = (1 << 48) + 77
        ok = check_papi_row_invariants(
            {"aperf": 77, "mperf": 77, "cpu_clock_est_cycles": cycles},
            type_name="host_cpu_hw",
            dev="0",
            arm=True,
        )
        self.assertFalse(any("cpu_clock_est_cycles" in w for w in ok), ok)
        bad = check_papi_row_invariants(
            {"aperf": 78, "cpu_clock_est_cycles": cycles}, type_name="host_cpu_hw", dev="0", arm=True
        )
        self.assertTrue(any("cpu_clock_est_cycles" in w for w in bad), bad)

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
            arm=True,
        )
        self.assertEqual(warns, [])

    def test_host_cpu_hw_devices_probe(self) -> None:
        from lib.host_live_probes import default_devices_for_type

        with patch(
            "lib.host_live_probes.probe_cpu_devices", return_value=["0", "1", "2"]
        ):
            self.assertEqual(default_devices_for_type("host_cpu_hw"), ["0", "1", "2"])

    def test_plausibility_accepts_measured_flops_without_scalar_keys(self) -> None:
        man = self._papi_manifest()
        schema = {"host_cpu_hw": man["types"]["host_cpu_hw"]["schema_keys"]}
        # ... fp_ops_retired / amd_fp_dp_ops (not built on Grace), pmu_enabled / running / solo
        # time (D6), then arm_fp_{dp,sp,hp}_spec.
        vals = ["5", "5", "5", "0", "0", "99", "1", "2", "-2", "-2", "9000", "7000", "7000", "7", "8", "9"]
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
        self.assertFalse(any("arm_est_flops" in w for w in warnings))


class CrossSampleSentinelTests(unittest.TestCase):
    _SCHEMA = {
        "host_net": ["rx_bytes,E,U=B", "tx_bytes,E,U=B"],
        "host_cpu_hw": ["instr_retired,E"],
    }

    def _manifest(self, slug: str) -> dict:
        return {
            "capability_slug": slug,
            "enable_slow_tier": False,
            "types": {t: {"schema_keys": k} for t, k in self._SCHEMA.items()},
        }

    @staticmethod
    def _body(ts: float, rx: str, instr: str = "5") -> str:
        return f"{ts:.6f} job1 h1\nhost_net eth0 {rx} 7\nhost_cpu_hw 0 {instr}\n"

    def _run(self, series, *, slug="x86_64-ver3.1-hw-x86perf-sent1-slowtier0", banner=1, cpu=False):
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        bodies = [
            self._body(1000.0 + 30 * i, "9" if cpu else v, v if cpu else "5")
            for i, v in enumerate(series)
        ]
        pair = SnapshotPair(
            kind="full",
            ts_a=1000.0,
            ts_b=1000.0 + 30 * (len(series) - 1),
            body_a=bodies[0],
            body_b=bodies[-1],
            bodies_mid=tuple(bodies[1:-1]),
        )
        return run_cross_sample_checks(
            self._manifest(slug),
            self._SCHEMA,
            timing=DaemonTiming(30.0, 60.0, 300.0, False),
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
            sentinel_banner=banner,
        )

    def test_chain_breaking_transition_is_exempt(self) -> None:
        notes, warnings, errors = self._run(["100", "-2", "50"])
        self.assertFalse(errors)
        self.assertFalse(any("decreased" in w for w in warnings), warnings)
        self.assertTrue(any("NOT_PRESENT=1" in n and "chain-break 1" in n for n in notes), notes)

    def test_bridgeable_decrease_fails(self) -> None:
        _, _, errors = self._run(["100", "-3", "90"])
        self.assertTrue(any(e.startswith("FAIL cross_sample bridge host_net/eth0 rx_bytes") for e in errors), errors)

    def test_bridgeable_advance_passes(self) -> None:
        notes, _, errors = self._run(["100", "-1", "-9", "150"])
        self.assertFalse(errors)
        self.assertTrue(any("BACKOFF=1 NOT_UPDATED=1" in n and "bridged 1" in n for n in notes), notes)

    def test_mixed_gap_with_one_chain_breaker_is_exempt(self) -> None:
        _, _, errors = self._run(["100", "-3", "-11", "90"])
        self.assertFalse(errors)

    def test_plain_decrease_still_warns(self) -> None:
        _, warnings, errors = self._run(["100", "90"])
        self.assertFalse(errors)
        self.assertTrue(any("decreased 100 -> 90" in w for w in warnings), warnings)

    def test_host_cpu_hw_bridge_violation_fails_on_every_backend(self) -> None:
        # Both CPU backends keep software lifetimes that bridge a gap, so no build downgrades a
        # host_cpu_hw bridge violation to a note (a slug-less manifest included).
        for slug in ("x86_64-ver3.1-hw-x86perf-sent1", "aarch64-ver3.1-hw-arm-sent1", ""):
            notes, _, errors = self._run(["100", "-3", "90"], cpu=True, slug=slug)
            self.assertTrue(any("host_cpu_hw/0 instr_retired" in e for e in errors), (slug, errors))
            self.assertFalse(any("informational" in n for n in notes), notes)

    def test_knob0_reports_skip_and_tokens_fail(self) -> None:
        notes, _, errors = self._run(["100", "150"], banner=None)
        self.assertFalse(errors)
        self.assertIn("SKIP cross_sample full sentinel bridge (emit_value_sentinels 0)", notes)
        self.assertFalse(any("PASS" in n and "sentinel" in n for n in notes), notes)
        _, _, errors = self._run(["100", "-3", "150"], banner=None)
        self.assertTrue(any("without a $sentinels banner" in e for e in errors), errors)

    def test_fixture_loader_reads_intermediate_captures(self) -> None:
        from lib.shm_snapshot import load_fixture_pair

        with tempfile.TemporaryDirectory() as td:
            for i, rx in enumerate(("1", "-3", "2")):
                (Path(td) / f"t{i}").mkdir()
                (Path(td) / f"t{i}" / "full").write_text(self._body(1000.0 + i, rx), encoding="utf-8")
            pair = load_fixture_pair(Path(td), "full")
        self.assertEqual(len(pair.bodies_mid), 1)
        self.assertIn(" -3 ", pair.bodies_mid[0])
        self.assertEqual(pair.ts_b, 1002.0)


class SentinelManifestLayerTests(unittest.TestCase):
    _SCHEMA = {
        "host_net": ["rx_bytes,E,U=B", "tx_bytes,E,U=B"],
        "host_vm": ["pgfault,E", "thp_split,E"],
        "host_ib": ["port_rcv_data,E,W=32", "sw_rx_bytes,E"],
        "amd_gpu": ["gpu_util"],
    }

    @staticmethod
    def _body(*rows: str, ts: float = 1000.0) -> str:
        return f"{ts:.6f} job1 h1\n" + "".join(r + "\n" for r in rows)

    def _manifest(self, slug: str) -> dict:
        from lib.sentinel_validate import allowed_sentinels

        allowed, expect = allowed_sentinels(slug, self._SCHEMA)
        return {"allowed_sentinels": allowed, "expect_not_built": expect}

    def _allow(self, body: str, slug="x86_64-ver3.1-hw-x86perf-sent1", strict=True, manifest=None):
        from lib.sentinel_validate import check_allowed_sentinels

        return check_allowed_sentinels(
            manifest if manifest is not None else self._manifest(slug),
            {"full": (body, False, None)},
            self._SCHEMA,
            strict=strict,
        )

    def test_allowed_sentinels_follow_the_build(self) -> None:
        from lib.sentinel_validate import allowed_sentinels

        allowed, expect = allowed_sentinels("x86_64-ver3.1-hw-x86perf-sent1", ["host_ib", "amd_gpu", "host_net"])
        self.assertIn(10, allowed["host_ib"]["*"])
        self.assertEqual(expect, {"host_ib": ["sw_*"], "amd_gpu": ["*"]})
        self.assertEqual(allowed["host_net"]["*"], [1, 2])
        allowed, expect = allowed_sentinels("x86_64-ver3.1-hw-ibdyn-x86perf-sent1", ["host_ib"])
        self.assertNotIn(10, allowed["host_ib"]["*"])
        self.assertEqual(expect, {})

    def test_opa_mad_build_allows_not_present_not_not_built(self) -> None:
        # An OPA MAD build with liboib_utils missing at runtime reports STL keys as NOT_PRESENT.
        from lib.sentinel_validate import NOT_BUILT, NOT_PRESENT, allowed_sentinels

        allowed, _ = allowed_sentinels("x86_64-ver3.1-hw-opa-opadyn-x86perf-sent1", ["host_opa"])
        self.assertIn(NOT_PRESENT, allowed["host_opa"]["*"])
        self.assertNotIn(NOT_BUILT, allowed["host_opa"]["*"])
        allowed, _ = allowed_sentinels("x86_64-ver3.1-hw-x86perf-sent1", ["host_opa"])
        self.assertIn(NOT_BUILT, allowed["host_opa"]["*"])

    def test_x86perf_host_cpu_hw_disallows_not_built(self) -> None:
        from lib.sentinel_validate import allowed_sentinels

        types = ["host_cpu_hw", "intel_x86_uncore_imc_skx"]
        allowed, _ = allowed_sentinels("x86_64-ver3.1-hw-x86perf-sent1", types)
        self.assertEqual(allowed["host_cpu_hw"]["*"], [1, 2])
        self.assertIn(10, allowed["intel_x86_uncore_imc_skx"]["*"])
        allowed, _ = allowed_sentinels("aarch64-ver3.1-hw-arm-sent1", types)
        self.assertIn(10, allowed["host_cpu_hw"]["*"])

    def test_unexpected_code_fails_strict_and_warns_otherwise(self) -> None:
        body = self._body("host_net eth0 -3 -2", "host_ib mlx5_0.1 -1 -10")
        _, warnings, errors = self._allow(body)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("host_net/eth0 rx_bytes: READ_FAILED", errors[0])
        _, warnings, errors = self._allow(body, strict=False)
        self.assertFalse(errors)
        self.assertTrue(warnings[0].startswith("WARN sentinel allowlist"))

    def test_not_built_with_mad_in_slug_fails(self) -> None:
        body = self._body("host_ib mlx5_0.1 7 -10")
        _, _, errors = self._allow(body, slug="x86_64-ver3.1-hw-ibdyn-x86perf-sent1")
        self.assertTrue(any("sw_rx_bytes: NOT_BUILT" in e for e in errors), errors)

    def test_expected_not_built_key_emitting_a_value_fails(self) -> None:
        notes, _, errors = self._allow(self._body("host_ib mlx5_0.1 7 -10", "amd_gpu 0 -10"))
        self.assertFalse(errors)
        self.assertIn("PASS sentinel allowlist", notes)
        _, _, errors = self._allow(self._body("host_ib mlx5_0.1 7 42", "amd_gpu 0 5"))
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(all("cannot read" in e for e in errors))

    def test_manifest_without_allowlist_skips(self) -> None:
        notes, warnings, errors = self._allow(self._body("host_net eth0 -3 -2"), manifest={})
        self.assertFalse(errors or warnings)
        self.assertTrue(notes[0].startswith("SKIP sentinel allowlist"))

    def test_not_present_probe_against_host_sources(self) -> None:
        from lib.sentinel_validate import check_not_present_probes

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "proc").mkdir()
            (root / "proc" / "vmstat").write_text("pgfault 10\n", encoding="utf-8")
            port = root / "sys" / "class" / "infiniband" / "mlx5_0" / "ports" / "1" / "counters"
            port.mkdir(parents=True)
            (port / "port_rcv_data").write_text("5\n", encoding="utf-8")
            body = self._body("host_vm - -2 -2", "host_ib mlx5_0.1 -2 -2")
            notes, errors = check_not_present_probes(
                {"full": (body, False, None)}, self._SCHEMA, proc_root=root / "proc", sys_root=root / "sys"
            )
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("host_vm/- pgfault" in e for e in errors))
        self.assertTrue(any("host_ib/mlx5_0.1 port_rcv_data" in e for e in errors))

    @staticmethod
    def _power_meter(root: Path, idx: int, label: str, uw: int) -> None:
        d = root / "class" / "hwmon" / f"hwmon{idx}"
        d.mkdir(parents=True)
        (d / "power1_oem_info").write_text(label + "\n", encoding="utf-8")
        (d / "power1_average").write_text(f"{uw}\n", encoding="utf-8")

    def test_not_present_probe_opa_hwmon_and_dcgm_skip(self) -> None:
        from lib.sentinel_validate import check_not_present_probes

        schema = {
            "host_opa": ["port_xmit_pkts,E", "port_rcv_data,E"],
            "host_power_socket": ["cpu_power_w", "socket_power_w"],
            "nvidia_gpu": ["gpu_util"],
        }
        with tempfile.TemporaryDirectory() as td:
            sys_root = Path(td) / "sys"
            counters = sys_root / "class" / "infiniband" / "hfi1_0" / "ports" / "1" / "counters"
            counters.mkdir(parents=True)
            (counters / "port_xmit_packets").write_text("4\n", encoding="utf-8")
            self._power_meter(sys_root, 0, "Grace Power Socket 0", 250000000)
            body = self._body("host_opa hfi1_0/1 -2 -2", "host_power_socket 0 -2 -2", "nvidia_gpu 0 -2")
            notes, errors = check_not_present_probes(
                {"full": (body, False, None)}, schema, proc_root=Path(td) / "proc", sys_root=sys_root
            )
        # Present sources (OPA *_packets file, Grace meter) FAIL; absent ones pass.
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("host_opa/hfi1_0/1 port_xmit_pkts" in e for e in errors))
        self.assertTrue(any("host_power_socket/0 socket_power_w" in e for e in errors))
        self.assertIn("SKIP sentinel probe nvidia_gpu (no DCGM field probe)", notes)

    def test_persistent_not_updated_names_the_site(self) -> None:
        from lib.sentinel_validate import check_persistent_not_updated, load_provenance

        a = self._body("host_net eth0 -1 5")
        b = self._body("host_net eth0 -1 -1", ts=1030.0)
        c = self._body("host_net eth0 7 -1", ts=1060.0)
        with tempfile.TemporaryDirectory() as td:
            prov_path = Path(td) / "sentinels"
            prov_path.write_text(
                "host_net eth0 rx_bytes 1 NOT_UPDATED 0 net.c:120 1000 1030 2\nshort line\n",
                encoding="utf-8",
            )
            prov = load_provenance(prov_path)
        self.assertEqual(prov, {("host_net", "eth0", "rx_bytes"): "net.c:120"})
        errors = check_persistent_not_updated([a, b, c], self._SCHEMA, require_tier=False, allowed_tier=None, provenance=prov)
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("host_net/eth0 rx_bytes (collector host_net, net.c:120)", errors[0])
        self.assertIn("tx_bytes (collector host_net, site unknown)", errors[1])
        self.assertFalse(check_persistent_not_updated([a, c], self._SCHEMA, require_tier=False, allowed_tier=None, provenance={}))
        self.assertEqual(load_provenance(Path("/nonexistent/sentinels")), {})

    def test_build_manifest_carries_allowlist(self) -> None:
        import build_message_expectations as bme

        caps = json.loads((SYNTHETIC / "monitor-build-capabilities.json").read_text(encoding="utf-8"))
        doc = bme.build_manifest(caps, shm_dir=SYNTHETIC, enable_slow_tier=True)
        self.assertIn("host_net", doc["allowed_sentinels"])
        self.assertEqual(doc["allowed_sentinels"]["host_net"]["*"], [1, 2])
        self.assertIn("expect_not_built", doc)


class IndependentReadTests(unittest.TestCase):
    _SCHEMA = {"host_cpu": ["user,E", "idle,E"], "host_ib": ["port_rcv_data,E,W=32"]}

    @staticmethod
    def _body(ts: float, user: int, idle: int, ib: int) -> str:
        return f"{ts:.6f} j h\nhost_cpu 0 {user} {idle}\nhost_ib mlx5_0.1 {ib}\n"

    def _check(self, live, *, a=(90, 50, 10), b=(110, 60, 20), t_read=1010.0):
        from lib.independent_read import check_independent_reads

        return check_independent_reads(
            self._SCHEMA, self._body(1000.0, *a), self._body(1030.0, *b), 1030.0, t_read, live,
            require_tier=False, allowed_tier=None,
        )

    def test_live_read_inside_emitted_window_passes(self) -> None:
        notes, errors = self._check({("host_cpu", "0", "user"): 100, ("host_ib", "mlx5_0.1", "port_rcv_data"): 15})
        self.assertFalse(errors)
        self.assertIn("PASS independent_read (2 keys)", notes)

    def test_read_outside_window_fails(self) -> None:
        _, errors = self._check({("host_cpu", "0", "user"): 200})
        self.assertEqual(len(errors), 1)
        self.assertIn("host_cpu/0 user: live 200 outside emitted [90, 110]", errors[0])

    def test_flat_zero_with_nonzero_source_fails(self) -> None:
        _, errors = self._check({("host_cpu", "0", "idle"): 40}, a=(90, 0, 10), b=(110, 0, 20))
        self.assertTrue(any("live 40 outside emitted [0, 0]" in e for e in errors), errors)

    def test_width_wrap_is_not_a_failure(self) -> None:
        top = (1 << 32) - 5
        _, errors = self._check({("host_ib", "mlx5_0.1", "port_rcv_data"): (1 << 32) - 1}, a=(90, 50, top), b=(110, 60, 3))
        self.assertFalse(errors)

    def test_read_after_sample_b_is_inconclusive(self) -> None:
        notes, errors = self._check({("host_cpu", "0", "user"): 500}, t_read=1031.0)
        self.assertFalse(errors)
        self.assertTrue(notes[0].startswith("NOTE independent_read"))
        notes, errors = self._check({})
        self.assertTrue(notes[0].startswith("SKIP independent_read"))

    def test_sources_read_from_proc_and_sysfs(self) -> None:
        from lib.independent_read import read_independent_sources

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "proc").mkdir()
            (root / "proc" / "stat").write_text(
                "cpu 1 2 3 4 5 6 7\ncpu0 11 12 13 14 15 16 17 0 0 0\nintr 5\n", encoding="utf-8"
            )
            c = root / "sys" / "class" / "infiniband" / "mlx5_0" / "ports" / "1" / "counters"
            c.mkdir(parents=True)
            (c / "port_rcv_data").write_text("77\n", encoding="utf-8")
            _, live = read_independent_sources(
                self._SCHEMA, self._body(1.0, 1, 1, 1), require_tier=False, allowed_tier=None,
                proc_root=root / "proc", sys_root=root / "sys",
            )
        self.assertEqual(live[("host_cpu", "0", "user")], 11)
        self.assertEqual(live[("host_cpu", "0", "softirq")], 17)
        self.assertNotIn(("host_cpu", "", "user"), live)
        self.assertEqual(live[("host_ib", "mlx5_0.1", "port_rcv_data")], 77)

    _GAUGE_SCHEMA = {
        "host_opa": ["port_xmit_data,E,W=64"],
        "host_block": ["rd_ios,e", "rd_merges,e", "rd_sectors,E,U=512B"],
        "host_mem": ["mem_total,U=KB", "mem_free,U=KB"],
        "host_power_socket": ["cpu_power_w", "socket_power_w"],
    }

    def _check_rows(self, rows_a: str, rows_b: str, live, t_read=1010.0):
        from lib.independent_read import check_independent_reads

        return check_independent_reads(
            self._GAUGE_SCHEMA, f"1000.0 j h\n{rows_a}", f"1030.0 j h\n{rows_b}", 1030.0, t_read, live,
            require_tier=False, allowed_tier=None,
        )

    def test_opa_and_block_counters_bracket(self) -> None:
        live = {("host_opa", "hfi1_0/1", "port_xmit_data"): 15, ("host_block", "sda", "rd_sectors"): 70}
        notes, errors = self._check_rows("host_opa hfi1_0/1 10\nhost_block sda 1 0 50\n",
                                         "host_opa hfi1_0/1 20\nhost_block sda 2 0 90\n", live)
        self.assertFalse(errors)
        _, errors = self._check_rows("host_opa hfi1_0/1 10\nhost_block sda 1 0 50\n",
                                     "host_opa hfi1_0/1 12\nhost_block sda 2 0 60\n", live)
        self.assertEqual(len(errors), 2, errors)

    def test_gauges_fail_only_on_flat_zero_with_live_source(self) -> None:
        live = {("host_mem", "0", "mem_total"): 8000000, ("host_power_socket", "0", "socket_power_w"): 250,
                ("host_block", "sda", "rd_ios"): 9}
        notes, errors = self._check_rows("host_mem 0 7999000 100\nhost_power_socket 0 90 240\nhost_block sda 3 0 1\n",
                                         "host_mem 0 7999000 100\nhost_power_socket 0 90 260\nhost_block sda 4 0 1\n", live)
        self.assertFalse(errors)
        self.assertIn("PASS independent_read (3 keys)", notes)
        _, errors = self._check_rows("host_mem 0 0 100\nhost_power_socket 0 90 0\nhost_block sda 3 0 1\n",
                                     "host_mem 0 0 100\nhost_power_socket 0 90 0\nhost_block sda 4 0 1\n", live)
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("host_mem/0 mem_total: emitted 0 in both captures, source reads 8000000" in e for e in errors))
        self.assertTrue(any("host_power_socket/0 socket_power_w" in e for e in errors))

    def test_new_sources_read_from_sysfs(self) -> None:
        from lib.independent_read import read_independent_sources

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            port = root / "class" / "infiniband" / "hfi1_0" / "ports" / "1"
            (port / "hw_counters").mkdir(parents=True)
            (port / "hw_counters" / "TxWords").write_text("1001\n", encoding="utf-8")
            (root / "block" / "sda").mkdir(parents=True)
            (root / "block" / "sda" / "stat").write_text("5 6 70 8 9 10 11 12 0 14 15\n", encoding="utf-8")
            node = root / "devices" / "system" / "node" / "node0"
            node.mkdir(parents=True)
            (node / "meminfo").write_text("Node 0 MemTotal:  8000000 kB\nNode 0 MemFree:   100 kB\n", encoding="utf-8")
            hw = root / "class" / "hwmon" / "hwmon0"
            hw.mkdir(parents=True)
            (hw / "power1_oem_info").write_text("CPU Power Socket 0\n", encoding="utf-8")
            (hw / "power1_average").write_text("99600000\n", encoding="utf-8")
            body = "1.0 j h\nhost_opa hfi1_0/1 1\nhost_block sda 1 1 1\nhost_mem 0 1 1\nhost_power_socket 0 1 1\n"
            _, live = read_independent_sources(
                self._GAUGE_SCHEMA, body, require_tier=False, allowed_tier=None,
                proc_root=root / "proc", sys_root=root,
            )
        self.assertEqual(live[("host_opa", "hfi1_0/1", "port_xmit_data")], 1001)
        self.assertEqual(live[("host_block", "sda", "rd_sectors")], 70)
        self.assertEqual(live[("host_block", "sda", "time_in_queue")], 15)
        self.assertEqual(live[("host_mem", "0", "mem_total")], 8000000)
        self.assertEqual(live[("host_power_socket", "0", "cpu_power_w")], 100)

    def test_capture_pair_runs_between_on_a_stable_capture(self) -> None:
        import threading

        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import capture_pair

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "full"
            path.write_text(self._body(1000.0, 1, 1, 1), encoding="utf-8")
            seen: list[str] = []

            def between(body: str) -> None:
                seen.append(body)
                if len(seen) == 1:  # a sample lands during the first call: it must be retried
                    path.write_text(self._body(1030.0, 2, 2, 2), encoding="utf-8")

            later = threading.Timer(0.3, lambda: path.write_text(self._body(1060.0, 3, 3, 3), encoding="utf-8"))
            later.start()
            pair = capture_pair(Path(td), "full", timing=DaemonTiming(30.0, 60.0, 300.0, False), between=between)
            later.join()
        self.assertEqual(len(seen), 2)
        self.assertEqual(pair.ts_a, 1030.0)
        self.assertEqual(pair.ts_b, 1060.0)


class IndependentReadIbMadTests(unittest.TestCase):
    """c317-016 (MAD build): emitted host_ib port_rcv_data is the 64-bit PortCountersExtended,
    a separate counter ~36.4 M behind sysfs counters/port_rcv_data while both advance at the
    same rate; the sysfs bracket must not FAIL that monitor, and must still FAIL on a non-MAD
    build or a MAD build whose MAD query failed (sysfs fed the key)."""

    _SCHEMA = {"host_ib": ["port_rcv_data,E,W=64,U=4B", "symbol_error,E,W=32", "port_select,c",
                           "port_xmit_pkts,E"]}
    _OFFSET = 36_430_379
    _MAD_SLUG = "x86_64-ver3.1-debug-hw-ib-ibdyn-x86perf-sent1-slowtier1"
    _SYSFS_SLUG = "x86_64-ver3.1-debug-hw-x86perf-sent1-slowtier1"

    @staticmethod
    def _body(ts: float, rcv: int, sym: int, mad: tuple[str, str]) -> str:
        return f"{ts:.6f} j h\nhost_ib mlx5_0.1 {rcv} {sym} {mad[0]} {mad[1]}\n"

    def _check(self, slug, *, live_rcv, live_sym=5, mad_a=("1", "900"), mad_b=("1", "950"), schema=None):
        from lib.independent_read import check_independent_reads

        schema = schema or self._SCHEMA
        live = {("host_ib", "mlx5_0.1", "port_rcv_data"): live_rcv,
                ("host_ib", "mlx5_0.1", "symbol_error"): live_sym}
        return check_independent_reads(
            schema, self._body(1000.0, 81_653_661_045, 4, mad_a),
            self._body(1030.0, 81_653_967_045, 6, mad_b), 1030.0, 1010.0, live,
            require_tier=False, allowed_tier=None, capability_slug=slug,
        )

    def test_mad_build_constant_offset_is_a_note_not_a_failure(self) -> None:
        notes, errors = self._check(self._MAD_SLUG, live_rcv=81_653_800_000 + self._OFFSET)
        self.assertEqual(errors, [])
        self.assertTrue(any(n.startswith("NOTE independent_read host_ib MAD PortCountersExtended")
                            and "mlx5_0.1 port_rcv_data" in n for n in notes), notes)
        self.assertIn("PASS independent_read (1 keys)", notes)

    def test_mad_build_still_brackets_sysfs_only_keys(self) -> None:
        _, errors = self._check(self._MAD_SLUG, live_rcv=81_653_800_000 + self._OFFSET, live_sym=9)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("host_ib/mlx5_0.1 symbol_error: live 9 outside emitted [4, 6]", errors[0])

    def test_non_mad_build_offset_still_fails(self) -> None:
        _, errors = self._check(self._SYSFS_SLUG, live_rcv=81_653_800_000 + self._OFFSET,
                                mad_a=("-10", "-10"), mad_b=("-10", "-10"))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("host_ib/mlx5_0.1 port_rcv_data: live", errors[0])
        notes, errors = self._check(self._SYSFS_SLUG, live_rcv=81_653_800_000,
                                    mad_a=("-10", "-10"), mad_b=("-10", "-10"))
        self.assertEqual(errors, [])
        self.assertIn("PASS independent_read (2 keys)", notes)

    def test_mad_build_with_failed_mad_query_brackets_sysfs_value(self) -> None:
        """MAD BACKEND_ERROR in both captures: port_rcv_data came from sysfs, so bracket it."""
        _, errors = self._check(self._MAD_SLUG, live_rcv=81_653_800_000 + self._OFFSET,
                                mad_a=("-7", "-7"), mad_b=("-7", "-7"))
        self.assertTrue(any("port_rcv_data: live" in e for e in errors), errors)

    def test_mad_filled_in_one_capture_is_not_bracketed(self) -> None:
        notes, errors = self._check(self._MAD_SLUG, live_rcv=81_653_800_000 + self._OFFSET,
                                    mad_a=("-7", "-7"))
        self.assertEqual(errors, [])
        self.assertTrue(any("MAD PortCountersExtended" in n for n in notes), notes)

    def test_without_slug_the_payload_decides(self) -> None:
        notes, errors = self._check(None, live_rcv=81_653_800_000 + self._OFFSET)
        self.assertEqual(errors, [])
        self.assertTrue(any("MAD PortCountersExtended" in n for n in notes), notes)
        _, errors = self._check(None, live_rcv=81_653_800_000 + self._OFFSET,
                                mad_a=("-10", "-10"), mad_b=("-10", "-10"))
        self.assertTrue(any("port_rcv_data: live" in e for e in errors), errors)

    def test_mad_slug_without_extended_keys_in_schema_is_not_bracketed(self) -> None:
        from lib.independent_read import check_independent_reads

        schema = {"host_ib": ["port_rcv_data,E,W=64"]}
        body = "{ts} j h\nhost_ib mlx5_0.1 {v}\n"
        live = {("host_ib", "mlx5_0.1", "port_rcv_data"): 500 + self._OFFSET}
        args = (schema, body.format(ts=1000.0, v=400), body.format(ts=1030.0, v=600), 1030.0, 1010.0, live)
        notes, errors = check_independent_reads(*args, require_tier=False, allowed_tier=None,
                                                capability_slug=self._MAD_SLUG)
        self.assertEqual(errors, [])
        self.assertTrue(any("MAD PortCountersExtended" in n for n in notes), notes)
        _, errors = check_independent_reads(*args, require_tier=False, allowed_tier=None,
                                            capability_slug=self._SYSFS_SLUG)
        self.assertEqual(len(errors), 1, errors)


def _x86_hw_schema() -> list[str]:
    """host_cpu_hw keys in cpu_counter_metrics.h order with their x86 widths and units."""
    w48 = ["instr_retired_any", "cycles_unhalted_core", "cycles_unhalted_ref",
           "mem_load_uops_retired_l1_hit", "mem_load_uops_retired_l2_hit",
           "mem_load_uops_retired_llc_hit", "l1d_replacement", "retired_instructions",
           "retired_branch_instr", "retired_misp_branch_instr", "ls_dispatch", "instr_retired",
           "aperf", "mperf"]
    fp = [f"fp_arith_inst_retired_{w}_{p}" for p in ("double", "single")
          for w in ("scalar", "128b_packed", "256b_packed", "512b_packed")]
    return ([f"{k},E,W=48" for k in w48 + fp] + ["cpu_clock_est_cycles,E,W=64",
            "arm_est_flops,E,W=64,U=FLOP", "arm_int8_ops,E,W=48", "arm_int16_ops,E,W=48",
            "fp_ops_retired,E,W=64,U=FLOP", "amd_fp_dp_ops,E,W=64,U=FLOP",
            "pmu_enabled_time,E,W=64,U=ns", "pmu_running_time,E,W=64,U=ns",
            "pmu_solo_running_time,E,W=64,U=ns"])


class X86DirectPerfValidationTests(unittest.TestCase):
    """x86 host_cpu_hw on direct perf: Intel FP_ARITH measured, AMD instr_retired / aperf from
    generic events, mperf NOT_PRESENT on AMD, W=48 lifetimes modulo 2^48, D6 sharing keys."""

    NP = "-2"  # NOT_PRESENT
    NB = "-10"  # NOT_BUILT
    E9 = 10**9

    def _row(self, **vals) -> list[str]:
        names = [schema_key_name(k) for k in _x86_hw_schema()]
        return [str(vals.get(n, self.NP)) for n in names]

    def _intel(self, **over) -> dict:
        base = {"instr_retired_any": 7000, "instr_retired": 7000, "cycles_unhalted_core": 9000,
                "aperf": 9000, "cycles_unhalted_ref": 8000, "mperf": 8000, "l1d_replacement": 10,
                "mem_load_uops_retired_l1_hit": 50, "mem_load_uops_retired_l2_hit": 5,
                "mem_load_uops_retired_llc_hit": 1, "pmu_enabled_time": 2 * self.E9,
                "pmu_running_time": self.E9, "pmu_solo_running_time": 2 * self.E9}
        base.update({f"fp_arith_inst_retired_{w}_{p}": 3 for p in ("double", "single")
                     for w in ("scalar", "128b_packed", "256b_packed", "512b_packed")})
        base.update(over)
        return base

    def _amd(self, **over) -> dict:
        base = {"retired_instructions": 7001, "retired_branch_instr": 900,
                "retired_misp_branch_instr": 9, "ls_dispatch": 3000, "instr_retired": 7000,
                "instr_retired_any": 7000, "aperf": 9000, "cycles_unhalted_core": 9000,
                "fp_ops_retired": 640, "amd_fp_dp_ops": 320,
                "pmu_enabled_time": 2 * self.E9, "pmu_running_time": self.E9,
                "pmu_solo_running_time": 2 * self.E9}
        base.update(over)
        return base

    def _manifest(self) -> dict:
        schema = _x86_hw_schema()
        return {"capability_slug": "x86_64-ver3.1-hw-x86perf-sent1-slowtier1",
                "enable_slow_tier": True,
                "compile_capabilities": {"configure": {"cpu_backend": "x86"}},
                "types": {"host_cpu_hw": {"schema_keys": schema,
                                          "schema_key_names": [schema_key_name(k) for k in schema],
                                          "devices": ["0"]}}}

    def _plaus(self, full_vals: dict, fast_vals: dict | None = None, *, dt=30.0):
        manifest = self._manifest()
        schema = {"host_cpu_hw": manifest["types"]["host_cpu_hw"]["schema_keys"]}
        full = f"1000.0 - h\nhost_cpu_hw 0 @full {' '.join(self._row(**full_vals))}\n"
        fast = None
        if fast_vals is not None:
            fast = f"{1000.0 - dt} - h\nhost_cpu_hw 0 @fast {' '.join(self._row(**fast_vals))}\n"
        return check_plausibility(manifest, schema, full_body=full, fast_body=fast, schema_body=None,
                                  no_freshness=True, strict=True)

    def test_x86_schema_contract_passes_with_new_keys(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        notes, errors = check_papi_schema_contract(self._manifest())
        self.assertEqual(errors, [])
        self.assertTrue(any("PASS papi schema" in n for n in notes), notes)

    def test_intel_and_amd_rows_are_plausible(self) -> None:
        for vals in (self._intel(), self._amd()):
            _, warnings, errors = self._plaus(vals)
            self.assertEqual((warnings, errors), ([], []), vals)

    def test_x86_aperf_mperf_differ_under_turbo(self) -> None:
        """Grace equates aperf and mperf; x86 cycles vs ref-cycles legitimately differ."""
        from lib.papi_shm_validate import check_papi_row_invariants

        self.assertEqual(check_papi_row_invariants(self._intel(), type_name="host_cpu_hw", dev="0",
                                                   arm=False), [])
        warns = check_papi_row_invariants({"aperf": 9000, "mperf": 8000}, type_name="host_cpu_hw",
                                          dev="0", arm=True)
        self.assertTrue(any("aperf=9000 != mperf=8000" in w for w in warns), warns)

    def test_same_event_keys_must_match(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        for a, b in (("instr_retired", "instr_retired_any"), ("aperf", "cycles_unhalted_core"),
                     ("mperf", "cycles_unhalted_ref")):
            vals = self._intel(**{b: self._intel()[b] + 1})
            warns = check_papi_row_invariants(vals, type_name="host_cpu_hw", dev="3", arm=False)
            self.assertTrue(any(f"host_cpu_hw/3: {a}=" in w and f"!= {b}=" in w for w in warns), warns)

    def test_enabled_time_bounds_catch_unit_errors(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        # 3e9 cycles over 2 s of enabled time published in us (2e6 "ns"): 1500 per ns.
        warns = check_papi_row_invariants(self._amd(aperf=3 * self.E9, pmu_enabled_time=2 * 10**6,
                                                    pmu_running_time=10**6, pmu_solo_running_time=10**6),
                                          type_name="host_cpu_hw", dev="0", arm=False)
        self.assertTrue(any("aperf=3000000000 > 10 per ns" in w for w in warns), warns)
        warns = check_papi_row_invariants(self._amd(fp_ops_retired=641 * 2 * self.E9),
                                          type_name="host_cpu_hw", dev="0", arm=False)
        self.assertTrue(any("fp_ops_retired=" in w and "640 per ns" in w for w in warns), warns)
        self.assertEqual(check_papi_row_invariants(self._amd(fp_ops_retired=640 * 2 * self.E9),
                                                   type_name="host_cpu_hw", dev="0", arm=False), [])

    def test_w48_key_above_2_48_warns_on_x86_only(self) -> None:
        _, _, errors = self._plaus(self._intel(l1d_replacement=1 << 48))
        self.assertTrue(any("l1d_replacement=281474976710656 >= 2^48" in e for e in errors), errors)
        _, _, errors = self._plaus(self._amd(fp_ops_retired=1 << 50))  # W=64 key: no range check
        self.assertFalse(any(">= 2^" in e for e in errors), errors)

    def test_uncore_rows_may_carry_pmu_times(self) -> None:
        from lib.papi_shm_validate import check_papi_row_invariants

        for t in ("intel_x86_uncore_imc_spr", "amd_x86_uncore_df_turin"):
            ok = {"cas_count_read": 5, "pmu_enabled_time": 100, "pmu_running_time": 100}
            self.assertEqual(check_papi_row_invariants(ok, type_name=t, dev="mbox0", arm=False), [])
            bad = dict(ok, pmu_running_time=101)
            warns = check_papi_row_invariants(bad, type_name=t, dev="mbox0", arm=False)
            self.assertEqual(warns, [f"{t}/mbox0: pmu_running_time=101 > pmu_enabled_time=100"])
        schema = {"intel_x86_uncore_imc_spr": ["cas_count_read,E,W=48", "pmu_enabled_time,E,W=64,U=ns",
                                               "pmu_running_time,E,W=64,U=ns"]}
        _, warnings, _ = check_plausibility(
            {"enable_slow_tier": False, "types": {}}, schema,
            full_body="1000.0 - h\nintel_x86_uncore_imc_spr mbox0 5 100 90\n", fast_body=None,
            schema_body=None, no_freshness=True, strict=False)
        self.assertEqual(warnings, [])

    def test_pair_rates_handle_w48_wrap(self) -> None:
        top = (1 << 48) - 1000
        old = self._intel(aperf=top, cycles_unhalted_core=top)
        # +3e9 cycles over 1 s (3 GHz) across the 2^48 wrap: plausible.
        wrapped = (top + 3 * self.E9) % (1 << 48)
        new = self._intel(aperf=wrapped, cycles_unhalted_core=wrapped,
                          pmu_enabled_time=4 * self.E9, pmu_running_time=2 * self.E9,
                          pmu_solo_running_time=4 * self.E9)
        _, warnings, errors = self._plaus(new, old, dt=1.0)
        self.assertEqual((warnings, errors), ([], []))
        # +12e9 cycles in 1 s: 12 GHz is above the ceiling.
        fast = (top + 12 * self.E9) % (1 << 48)
        new = dict(new, aperf=fast, cycles_unhalted_core=fast)
        _, _, errors = self._plaus(new, old, dt=1.0)
        self.assertTrue(any("aperf rate 1.2e+10/s > 1e+10/s" in e for e in errors), errors)

    def test_pair_rates_skip_grace_cycle_keys(self) -> None:
        """Grace aperf flips between measured CPU_CYCLES and the util x freq estimate, so only its
        FLOP and sharing-time rates are bounded."""
        from lib.papi_shm_validate import check_host_cpu_hw_rates

        old = {"aperf": 0, "arm_est_flops": 0, "pmu_enabled_time": 0, "pmu_running_time": 0}
        new = {"aperf": 10**12, "arm_est_flops": 10**15, "pmu_enabled_time": 5, "pmu_running_time": 6}
        warns = check_host_cpu_hw_rates(old, new, 1.0, {"aperf": 48}, dev="0", arm=True)
        self.assertFalse(any("aperf" in w for w in warns), warns)
        self.assertTrue(any("arm_est_flops rate" in w for w in warns), warns)
        self.assertTrue(any("pmu_running_time advanced 6 ns > pmu_enabled_time 5 ns" in w for w in warns), warns)
        warns = check_host_cpu_hw_rates(old, new, 1.0, {"aperf": 48}, dev="0", arm=False)
        self.assertTrue(any("aperf rate" in w for w in warns), warns)

    def test_pair_rates_running_cannot_outpace_enabled(self) -> None:
        old = self._amd()
        new = self._amd(pmu_enabled_time=3 * self.E9, pmu_running_time=3 * self.E9,
                        pmu_solo_running_time=3 * self.E9)
        _, _, errors = self._plaus(new, old, dt=30.0)
        self.assertTrue(any("pmu_running_time advanced 2000000000 ns > pmu_enabled_time 1000000000 ns"
                            in e for e in errors), errors)

    def test_pair_rates_skip_decrease_and_close_timestamps(self) -> None:
        old = self._amd(aperf=10_000)
        new = self._amd(aperf=9_000)  # a decrease is the cross-sample layer's finding
        _, warnings, errors = self._plaus(new, old, dt=30.0)
        self.assertEqual((warnings, errors), ([], []))
        _, warnings, errors = self._plaus(self._amd(aperf=10**12), old, dt=0.5)
        self.assertFalse(any("rate" in e for e in errors), errors)

    def test_vendor_key_sets(self) -> None:
        from lib.papi_shm_validate import check_x86_host_cpu_hw_key_sets as ks

        names = [schema_key_name(k) for k in _x86_hw_schema()]
        self.assertEqual(ks("intel", self._row(**self._intel()), names, dev="0"), [])
        self.assertEqual(ks("amd", self._row(**self._amd()), names, dev="0"), [])
        bad = ks("amd", self._row(**self._amd(mperf=5)), names, dev="0")
        self.assertEqual(bad, ["host_cpu_hw/0 mperf=5 on an AMD CPU (no such event)"])
        bad = ks("intel", self._row(**self._intel(fp_ops_retired=8)), names, dev="0")
        self.assertEqual(bad, ["host_cpu_hw/0 fp_ops_retired=8 on an INTEL CPU (no such event)"])
        # Intel FP_ARITH is programmed now: NOT_BUILT there is an old-reader leftover.
        bad = ks("intel", self._row(**self._intel(fp_arith_inst_retired_scalar_double=self.NB)), names, dev="0")
        self.assertEqual(bad, ["host_cpu_hw/0 fp_arith_inst_retired_scalar_double is NOT_BUILT on an "
                               "INTEL CPU (direct perf reads it)"])
        # AMD reads instr_retired / aperf from generic events.
        bad = ks("amd", self._row(**self._amd(instr_retired=self.NB, aperf=self.NB)), names, dev="0")
        self.assertEqual(len(bad), 2, bad)
        # NOT_PRESENT (unknown model) is fine; non-x86 vendors are skipped.
        self.assertEqual(ks("intel", self._row(), names, dev="0"), [])
        self.assertEqual(ks(None, self._row(mperf=1, fp_ops_retired=1), names, dev="0"), [])

    def test_live_spot_runs_vendor_key_sets(self) -> None:
        import time as _time

        from lib import live_spot_check as lsc

        manifest = self._manifest()
        schema = {"host_cpu_hw": manifest["types"]["host_cpu_hw"]["schema_keys"]}
        body = f"{_time.time():.1f} - h\nhost_cpu_hw 0 @full {' '.join(self._row(**self._amd(mperf=5)))}\n"
        with patch.object(lsc, "probe_x86_cpu_vendor", return_value="amd"), \
                patch.object(lsc, "probe_power_socket_devices", return_value=[]), \
                patch.object(lsc, "probe_nvidia_gpu_devices", return_value=[]), \
                patch.object(lsc, "probe_cpu_devices", return_value=[]), \
                patch.object(lsc, "probe_loadavg_scaled", return_value={}), \
                patch.object(lsc, "probe_host_fqdn", return_value=""):
            _, _, errors = lsc.run_live_spot_checks(manifest, schema, full_body=body, max_age_sec=300,
                                                    strict=True)
        self.assertEqual(errors, ["WARN live_spot host_cpu_hw/0 mperf=5 on an AMD CPU (no such event)"])

    def test_probe_x86_cpu_vendor(self) -> None:
        from lib.host_live_probes import probe_x86_cpu_vendor

        with tempfile.TemporaryDirectory() as td:
            info = Path(td) / "cpuinfo"
            for text, want in (("processor\t: 0\nvendor_id\t: GenuineIntel\n", "intel"),
                               ("vendor_id\t: AuthenticAMD\ncpu family\t: 26\n", "amd"),
                               ("processor\t: 0\nCPU implementer\t: 0x41\n", None)):
                info.write_text(text, encoding="utf-8")
                self.assertEqual(probe_x86_cpu_vendor(str(info)), want)
            self.assertIsNone(probe_x86_cpu_vendor(str(Path(td) / "missing")))


class FaultInjectSitesTests(unittest.TestCase):
    def _sites(self, lines, types, ci):
        import fault_inject_check as fic

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sentinel_sites"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch.object(fic, "CI_COVERED_FILES", frozenset(ci)):
                return fic.check_sites(path, {"types": {t: {} for t in types}})

    def test_ci_covered_file_is_a_note(self) -> None:
        notes, errors = self._sites(["net.c:139 0", "ps.c:54 2"], ["host_net"], {"net.c"})
        self.assertFalse(errors)
        self.assertEqual(notes, ["NOTE fault_inject site net.c:139 never fired (covered by CI matrix)"])

    def test_inactive_collector_is_a_note_and_active_one_fails(self) -> None:
        notes, errors = self._sites(["net.c:139 0"], ["host_ps"], set())
        self.assertFalse(errors)
        self.assertIn("collector host_net not active", notes[0])
        _, errors = self._sites(["net.c:139 0"], ["host_net"], set())
        self.assertEqual(errors, ["FAIL fault_inject site net.c:139 never fired"])

    def test_unknown_file_fails_and_missing_file_fails(self) -> None:
        _, errors = self._sites(["hps_new.c:3 0"], ["host_ps"], set())
        self.assertEqual(errors, ["FAIL fault_inject site hps_new.c:3 never fired"])
        import fault_inject_check as fic

        _, errors = fic.check_sites(Path("/nonexistent/sentinel_sites"), {"types": {}})
        self.assertTrue(errors[0].startswith("FAIL fault_inject: no sentinel_sites"))

    def test_plan_forces_a_core_open_on_each_backend(self) -> None:
        import fault_inject_check as fic

        manifest = {"types": {"host_cpu_hw": {"schema_keys": ["instr_retired,E,W=48"]}}}
        with tempfile.TemporaryDirectory() as td:
            for backend, spec, keys in (
                ("x86", "perf_open:instructions!16", ["instr_retired", "instr_retired_any"]),
                ("arm", "perf_open:fp_dp_spec!16", ["arm_fp_dp_spec"]),
            ):
                plan = fic.build_plan({"configure": {"cpu_backend": backend}}, manifest, ticks=3,
                                      sys_root=Path(td))
                self.assertEqual([(e["spec"], e["keys"], e["resume"]) for e in plan],
                                 [(spec, keys, False)])
                self.assertEqual(plan[0]["codes"], [fic.PMU_BUSY])

    def test_validator_passes_the_slug_to_independent_reads(self) -> None:
        # Regression: validate_shm_messages.py left capability_slug unset, so host_ib MAD sourcing
        # was guessed from the payload instead of the build's ib / ibdyn tokens.
        import ast

        tree = ast.parse((Path(__file__).resolve().parents[1] / "scripts" / "validate_shm_messages.py")
                         .read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "check_independent_reads"]
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("capability_slug", [k.arg for k in call.keywords])

    def test_ci_list_matches_makefile(self) -> None:
        import io
        from contextlib import redirect_stdout

        import check_fault_ci_files as c

        with redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(), 0)


class EmitBuildCapabilitiesFleetTests(unittest.TestCase):
    def test_slug_tokens_dyn_intel(self) -> None:
        features = {"HARDWARE", "INFINIBAND", "OPA", "INTEL_GPU", "GPU"}
        slug = ebc.build_capability_slug(
            arch="x86_64",
            version="1.0",
            debug=True,
            features=features,
            cpu_backend="x86",
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

    def test_slug_carries_sentinel_catalog_token(self) -> None:
        version = ebc.sentinel_catalog_version()
        self.assertGreaterEqual(version, 1)
        slug = ebc.build_capability_slug(
            arch="x86_64",
            version="1.0",
            debug=False,
            features={"HARDWARE"},
            cpu_backend="x86",
            tier="slowtier1",
            sentinel_version=version,
        )
        self.assertTrue(slug.endswith(f"-x86perf-sent{version}-slowtier1"), slug)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                ebc.sentinel_catalog_version(Path(td))

    def test_write_header_matches_slug(self) -> None:
        slug = "x86_64-ver3.0-debug-hw-slowtier1"
        with tempfile.TemporaryDirectory() as td:
            hdr = Path(td) / "monitor_capability_slug.h"
            ebc.write_capability_slug_header(hdr, slug)
            text = hdr.read_text(encoding="utf-8")
            self.assertIn(f'#define MONITOR_CAPABILITY_SLUG "{slug}"', text)
            self.assertIn("MONITOR_CAPABILITY_SLUG_H_", text)

    def test_slug_includes_beegfs(self) -> None:
        slug = ebc.build_capability_slug(
            arch="x86_64",
            version="1.0",
            debug=False,
            features={"LUSTRE", "BEEGFS"},
            cpu_backend=None,
            tier="slowtier1",
        )
        self.assertIn("-lustre-", f"-{slug}-")
        self.assertIn("-beegfs-", f"-{slug}-")

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

    def test_fleet_ls6_signature(self) -> None:
        self.assertEqual(
            ebc._fleet_ls6(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU"},
            ),
            "ls6",
        )
        self.assertIsNone(
            ebc._fleet_ls6(
                ib_mad_dlopen=True,
                opa_mad_dlopen=True,
                features={"GPU"},
            )
        )
        self.assertIsNone(
            ebc._fleet_ls6(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU", "INTEL_GPU"},
            )
        )
        self.assertIsNone(
            ebc._fleet_ls6(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU", "AMD_GPU"},
            )
        )
        self.assertIsNone(
            ebc._fleet_ls6(
                ib_mad_dlopen=False,
                opa_mad_dlopen=False,
                features={"GPU"},
            )
        )
        self.assertIsNone(
            ebc._fleet_ls6(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU"},
                shared_node=True,
            )
        )

    def test_fleet_horizon_signature(self) -> None:
        self.assertEqual(
            ebc._fleet_horizon(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU"},
                shared_node=True,
            ),
            "horizon",
        )
        self.assertIsNone(
            ebc._fleet_horizon(
                ib_mad_dlopen=True,
                opa_mad_dlopen=False,
                features={"GPU"},
                shared_node=False,
            )
        )
        self.assertIsNone(
            ebc._fleet_horizon(
                ib_mad_dlopen=True,
                opa_mad_dlopen=True,
                features={"GPU"},
                shared_node=True,
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
                "-DMONITOR_OPA_MAD_DLOPEN -DMONITOR_CPU_BACKEND_ARM -DDEBUG\n",
                encoding="utf-8",
            )
            features, cpu, debug, ibdyn, opadyn, shared = ebc._parse_src_makefile(
                td
            )
            self.assertIn("INTEL_GPU", features)
            self.assertTrue(ibdyn)
            self.assertTrue(opadyn)
            self.assertTrue(debug)
            self.assertEqual(cpu, "arm")
            self.assertFalse(shared)

    def test_parse_makefile_x86_backend_slug_token(self) -> None:
        """The x86 backend macro carries a digit: it must parse whole (configure.cpu_backend "x86")
        and the slug names it x86perf."""
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DMONITOR_WITH_HARDWARE -DMONITOR_CPU_BACKEND_X86\n",
                encoding="utf-8",
            )
            features, cpu, _d, _i, _o, shared = ebc._parse_src_makefile(td)
            self.assertEqual(cpu, "x86")
            self.assertFalse(shared)
        slug = ebc.build_capability_slug(
            arch="x86_64", version="3.1", debug=False, features=features, cpu_backend=cpu,
            tier="slowtier1",
        )
        self.assertIn("-hw-x86perf-", slug)
        self.assertNotIn("-x86-", f"-{slug}-")

    def test_parse_makefile_shared_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DMONITOR_WITH_GPU -DMONITOR_IB_MAD_DLOPEN "
                "-DMONITOR_SHARED_NODE_MULTI_JID\n",
                encoding="utf-8",
            )
            _f, _c, _d, ibdyn, opadyn, shared = ebc._parse_src_makefile(td)
            self.assertTrue(ibdyn)
            self.assertFalse(opadyn)
            self.assertTrue(shared)

    def test_parse_makefile_ignores_disabled_automake_conditionals(self) -> None:
        """Regression: release builds were stamped debug/shared-node from `#`-commented
        Automake conditional branches (`@DEBUG_TRUE@` -> `#` when the condition is false)."""
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DHAVE_CONFIG_H\n"
                "#am__append_1 = \\\n"
                "#\t-DDEBUG\n"
                "#am__append_3 = -DMONITOR_SHARED_NODE_MULTI_JID\n"
                "#am__append_5 = -DMONITOR_WITH_OPA -DMONITOR_IB_MAD_DLOPEN\n"
                "am__append_4 = -DMONITOR_WITH_GPU -DMONITOR_CPU_BACKEND_ARM\n",
                encoding="utf-8",
            )
            features, cpu, debug, ibdyn, _o, shared = ebc._parse_src_makefile(td)
            self.assertFalse(debug)
            self.assertFalse(shared)
            self.assertFalse(ibdyn)
            self.assertEqual(features, {"GPU"})
            self.assertEqual(cpu, "arm")


class WaitForShmFreshnessTests(unittest.TestCase):
    """Regression: after an RPM restart the new daemon's schema sat next to the old
    daemon's fast, with fewer host_cpu_hw values, until the first fast tick."""

    @staticmethod
    def _write(shm: Path, name: str, ts: str) -> None:
        if name == "schema":
            body = f"\n$hpcperfstats 3.0\n$hostname h\n!host_ps ctxt,E\n\n{ts} job h\nhost_ps - 1\n"
        else:
            body = f"\n{ts} job h\nhost_ps - @fast 1\n"
        (shm / name).write_text(body, encoding="utf-8")

    def _shm(self, td: str, schema_ts: str, fast_ts: str, full_ts: str) -> Path:
        shm = Path(td)
        self._write(shm, "schema", schema_ts)
        self._write(shm, "fast", fast_ts)
        self._write(shm, "full", full_ts)
        return shm

    def test_stale_fast_times_out_naming_file(self) -> None:
        import validate_shm_messages as vsm

        with tempfile.TemporaryDirectory() as td:
            shm = self._shm(td, "200.5", "100.0", "200.5")
            with self.assertRaises(TimeoutError) as ctx:
                vsm.wait_for_shm(shm, enable_slow=True, timeout_sec=0.3)
            msg = str(ctx.exception)
            self.assertIn("stale", msg)
            self.assertIn("fast", msg)
            self.assertNotIn("full", msg.split("stale", 1)[1])

    def test_stale_fast_ignored_without_slow_tier(self) -> None:
        import validate_shm_messages as vsm

        with tempfile.TemporaryDirectory() as td:
            shm = self._shm(td, "200.5", "100.0", "300.0")
            vsm.wait_for_shm(shm, enable_slow=False, timeout_sec=0.3)

    def test_equal_or_newer_payloads_pass(self) -> None:
        import validate_shm_messages as vsm

        with tempfile.TemporaryDirectory() as td:
            shm = self._shm(td, "200.5", "200.5", "230.5")
            vsm.wait_for_shm(shm, enable_slow=True, timeout_sec=0.3)

    def test_missing_fast_after_clean_start_still_times_out(self) -> None:
        import validate_shm_messages as vsm

        with tempfile.TemporaryDirectory() as td:
            shm = self._shm(td, "200.5", "200.5", "200.5")
            (shm / "fast").unlink()
            with self.assertRaises(TimeoutError) as ctx:
                vsm.wait_for_shm(shm, enable_slow=True, timeout_sec=0.3)
            self.assertIn("missing ['fast']", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
