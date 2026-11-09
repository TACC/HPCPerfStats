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
        )
        with patch("lib.host_live_probes._read_lines", return_value=mountstats.splitlines()):
            self.assertEqual(probe_nfs_mount_devices(), ["/home1", "/home2"])

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

    def test_allow_flat_zero_silences_errors(self) -> None:
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
        self.assertTrue(any("rx_bytes" in w and "flat" in w for w in warnings))
        self.assertFalse(any("rx_errors" in w for w in warnings))
        self.assertFalse(any("tx_errors" in w for w in warnings))
        self.assertTrue(any("allow-flat-zero" in n for n in notes))

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

    def _idle_pair_warnings(self, types: dict, body_a: str, body_b: str) -> list[str]:
        from lib.cross_sample_validate import run_cross_sample_checks
        from lib.daemon_conf import DaemonTiming
        from lib.shm_snapshot import SnapshotPair

        manifest = {"enable_slow_tier": True, "types": {
            t: {"schema_keys": k, "schema_key_names": [x.split(",")[0] for x in k]}
            for t, k in types.items()}}
        pair = SnapshotPair(kind="full", ts_a=1000.0, ts_b=1060.0,
                            body_a="1000.0 - h\n" + body_a, body_b="1060.0 - h\n" + body_b)
        _, warnings, errors = run_cross_sample_checks(
            manifest, types, timing=DaemonTiming(30.0, 60.0, 300.0, True), fast_pair=None,
            full_pair=pair, strict=False, active_conf_note="test")
        self.assertFalse(errors)
        return warnings

    def test_idle_cpu_without_user_time_silences_pmu_flats(self) -> None:
        """c122-018 2026-11-09 CPU 99: sys +10118 us, user flat -> instr/mperf flat expected."""
        keys = {"host_cpu_hw": ["cpu_util_total_accum_us,E", "cpu_util_user_accum_us,E",
                                "cpu_util_sys_accum_us,E", "instr_retired,E", "mperf,E"]}
        a = "host_cpu_hw 99 @full 58769178 58769178 0 177508607436 54937145200\n"
        b = "host_cpu_hw 99 @full 58779296 58769178 10118 177508607436 54937145200\n"
        self.assertEqual(self._idle_pair_warnings(keys, a, b), [])
        busy = "host_cpu_hw 99 @full 58779296 58779178 100 177508607436 54937145200\n"
        got = self._idle_pair_warnings(keys, a, busy)
        self.assertTrue(any("host_cpu_hw/99 instr_retired: flat" in w for w in got), got)

    def test_idle_gpu_and_unused_numa_silence_flats(self) -> None:
        keys = {"nvidia_gpu": ["gpu_util", "gpu_nvlink_tx_bytes,E,W=64,U=B", "gpu_flops,E,W=64"],
                "host_numa": ["numa_hit,E", "numa_miss,E"]}
        a = "nvidia_gpu 0 @full 0 157 437\nhost_numa 8 @full 5 7\n"
        self.assertEqual(self._idle_pair_warnings(keys, a, a), [])
        busy = "nvidia_gpu 0 @full 40 157 437\nhost_numa 8 @full 5 7\n"
        got = self._idle_pair_warnings(keys, a, busy)
        self.assertTrue(any("nvidia_gpu/0 gpu_nvlink_tx_bytes: flat" in w for w in got), got)
        half = "nvidia_gpu 0 @full 0 157 437\nhost_numa 8 @full 6 7\n"
        got = self._idle_pair_warnings(keys, a, half)
        self.assertTrue(any("host_numa/8 numa_miss: flat" in w for w in got), got)

    def test_host_cpu_nonzero_flat_still_warns(self) -> None:
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
        _, warnings, _ = run_cross_sample_checks(
            manifest,
            schema,
            timing=timing,
            fast_pair=None,
            full_pair=pair,
            strict=False,
            active_conf_note="test",
        )
        self.assertTrue(any("user" in w and "flat at 10" in w for w in warnings))

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
                "configure": {"likwid_overlay": True, "cpu_backend": "arm"}
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
            "compile_capabilities": {"configure": {"likwid_overlay": True}},
        }
        _, errors = check_papi_schema_contract(man)
        self.assertTrue(any("missing type" in e for e in errors))

    def test_legacy_papi_hybrid_alias_still_accepted(self) -> None:
        from lib.papi_shm_validate import check_papi_schema_contract

        man = self._papi_manifest()
        man["compile_capabilities"] = {"configure": {"papi_hybrid": True}}
        notes, errors = check_papi_schema_contract(man)
        self.assertEqual(errors, [])
        self.assertTrue(any("PASS papi schema" in n for n in notes))

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
                "-DMONITOR_OPA_MAD_DLOPEN -DMONITOR_CPU_BACKEND_LIKWID -DDEBUG\n",
                encoding="utf-8",
            )
            features, cpu, debug, ibdyn, opadyn, overlay, shared = ebc._parse_src_makefile(
                td
            )
            self.assertIn("INTEL_GPU", features)
            self.assertTrue(ibdyn)
            self.assertTrue(opadyn)
            self.assertTrue(debug)
            self.assertEqual(cpu, "likwid")
            self.assertFalse(overlay)
            self.assertFalse(shared)

    def test_parse_makefile_likwid_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                "DEFS = -DMONITOR_CPU_BACKEND_ARM -DMONITOR_CPU_LIKWID_OVERLAY\n",
                encoding="utf-8",
            )
            _f, cpu, _d, _i, _o, overlay, shared = ebc._parse_src_makefile(td)
            self.assertEqual(cpu, "arm")
            self.assertTrue(overlay)
            self.assertFalse(shared)

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
            _f, _c, _d, ibdyn, opadyn, _o, shared = ebc._parse_src_makefile(td)
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
                "am__append_4 = -DMONITOR_WITH_GPU -DMONITOR_CPU_BACKEND_LIKWID\n",
                encoding="utf-8",
            )
            features, cpu, debug, ibdyn, _o, _ov, shared = ebc._parse_src_makefile(td)
            self.assertFalse(debug)
            self.assertFalse(shared)
            self.assertFalse(ibdyn)
            self.assertEqual(features, {"GPU"})
            self.assertEqual(cpu, "likwid")


if __name__ == "__main__":
    unittest.main()
