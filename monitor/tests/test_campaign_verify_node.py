#!/usr/bin/env python3
"""scripts/campaign_verify_node.sh on a fake checkout with stub commands on PATH.

The script replaces two operator paste blocks (pastes over 4 KB lose lines on cluster nodes,
c313-031 2026-11-23). These tests lock its gates, the Genoa / GPU branches and the guarded
debug RPM erase. Python 3.6 compatible (run by `make check` on EL8 nodes).
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "campaign_verify_node.sh"
GENOA = "AMD EPYC 9684X 96-Core Processor"
MILAN = "AMD EPYC 7763 64-Core Processor"
GB_ROW = "{10, 0, 128, {128, 256, 512, 8192, 128}}"

STUBS = {
    "hostname": '[ "$1" = -f ] && echo c317-001.ls6.example || echo c317-001',
    "git": 'echo "git $*" >>"$CALLS"',
    "lscpu": ('case "$1" in -p=*) printf "# CPU,SOCKET\\n0,0\\n1,0\\n2,1\\n";; '
              '*) printf "Model name:  %s\\nSocket(s): 2\\n" "$CPU_MODEL";; esac'),
    "nvidia-smi": ('if [ "$1" = -L ]; then i=0; while [ "$i" -lt "${NGPU:-0}" ]; do '
                   'echo "GPU $i: NVIDIA A100"; i=$((i + 1)); done; else echo "0, A100"; fi'),
    "systemctl": 'echo "systemctl $*" >>"$CALLS"; [ "$1" = is-active ] && echo active; true',
    "rpm": 'echo "rpm $*" >>"$CALLS"; echo hpcperfstatsd-3.1-1.el8.x86_64',
    "timeout": 'shift; exec "$@"',
    # LATE_SRC: the daemon restart left `fast` missing; it reappears at the 3rd sleep.
    "sleep": ('echo "sleep $*" >>"$CALLS"; if [ -n "${LATE_SRC:-}" ]; then '
              'n=$(grep -c "^sleep" "$CALLS"); [ "$n" -ge 3 ] && '
              'cp "$LATE_SRC" "$HPCS_DEBUG_SHM/fast"; fi; true'),
    "perf": 'echo "perf $*" >>"$CALLS"; echo "7,,amd_df/event=0x1f,umask=0xffe/"',
    "taskset": "true",
    "dcgmi": 'echo "dcgmi $*"',
    "cmake": "true",
    "dcgmproftester13": 'echo "tester $*" >>"$CALLS"; exit "${TESTER_RC:-0}"',
}
TREE_SCRIPTS = {
    "site_conf.sh": 'echo "site_conf $*" >>"$CALLS"; exit "${GUARD_RC:-0}"',
    "prepare_rpmbuild_ls6.sh": ('echo "prep ls6 $* fleet=$HPCS_BUNDLE_FLEET '
                                'allow=${HPCS_ALLOW_DEFAULT_BROKER:-}" >>"$CALLS"; '
                                'exit "${PREP_RC:-0}"'),
    "prepare_rpmbuild_horizon.sh": ('echo "prep horizon $* fleet=$HPCS_BUNDLE_FLEET" >>"$CALLS"; '
                                    'exit "${PREP_RC:-0}"'),
    "rpm_debug_shm_verify.sh": (
        'echo "verify RPM_BUILD=$RPM_BUILD checks=${PERF_RAW_FLOPS_CHECK-on}'
        '${SCF_PERF_CHECK-on}${PMU_SHARING_CHECK-on}${FP_SLICES_CHECK-on}" >>"$CALLS"; '
        'if [ -n "${WRITE_JSON:-}" ]; then mkdir -p "$REPORT_DIR"; '
        'printf \'%s\' "$WRITE_JSON" >"$REPORT_DIR/correctness_c317-001_x.json"; fi; '
        'exit "${VERIFY_RC:-0}"'),
    "canary_gemm_load.sh": "true",
}
# Real tools the script and the stubs use; PATH holds only these and the stubs, so a host
# dcgmproftester / nvidia-smi / perf never leaks in.
BASE_TOOLS = ("bash", "awk", "cat", "chmod", "cp", "cut", "date", "dirname", "echo", "env", "find",
              "grep", "head", "ln", "ls", "mkdir", "paste", "python3", "rm", "sed", "sort",
              "tail", "touch", "tr", "uniq", "wc", "xargs")
JSON = ('{"overall": "PASS", "counts": {"PASS": 1}, "capability_slug": "x86perf", '
        '"checks": [{"status": "PASS", "name": "memory_bw", "ratio": 1.0, "reason": null}]}')


def _exe(path, body):
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class CampaignVerifyNode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mon = self.tmp / "w" / "monitor"
        scripts = self.mon / "scripts"
        (scripts / "fleet").mkdir(parents=True)
        shutil.copy(str(SCRIPT), str(scripts / SCRIPT.name))
        for name, body in TREE_SCRIPTS.items():
            _exe(scripts / name, body)
        for marker in ("stampede3", "ls6", "horizon"):
            (scripts / "fleet" / (marker + ".force")).touch()
        src = self.mon / "src"
        src.mkdir()
        (src / "hpcperfstats.conf").write_text(
            "server stats.example\nqueue ls6\nport 5672\nuser guest\npassword secret\n"
            "sample_freq 600\n")
        (self.mon / "hpcperfstats.spec").write_text("Name: hpcperfstats\n")
        (src / "job_shared_emit.c").write_text("void job_gpu_assign_job_set(void);\n")
        (src / "amd_x86_uncore_df.c").write_text("/* genoa uses df_zen5 */\n")
        (src / "nvidia_gpu_estimate.c").write_text("  " + GB_ROW + ",\n")
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        for name, body in STUBS.items():
            _exe(stubs / name, body)
        sysbin = self.tmp / "sysbin"
        sysbin.mkdir()
        for tool in BASE_TOOLS:
            os.symlink(shutil.which(tool), str(sysbin / tool))
        shm = self.tmp / "shm"
        shm.mkdir()
        (shm / "schema").write_text("!nvidia_gpu sm_active,E fp64,E,R=S power,E\n")
        (shm / "fast").write_text("100.0 1 c317-001\nnvidia_gpu 0 @fast 5 9\n")
        (shm / "full").write_text("100.0 1 c317-001\nnvidia_gpu 0 @full 5 7 9\n")
        df = self.tmp / "amd_df"
        (df / "format").mkdir(parents=True)
        (df / "cpumask").write_text("0\n")
        (df / "format" / "event").write_text("config:0-7\n")
        (df / "format" / "umask").write_text("config:8-15\n")
        for d in ("bak", "tmp"):
            (self.tmp / d).mkdir()

    def tearDown(self):
        shutil.rmtree(str(self.tmp))

    def run_node(self, node="ls6", cpu=MILAN, **env):
        full = {"PATH": "{}:{}".format(self.tmp / "stubs", self.tmp / "sysbin"),
                "HOME": str(self.tmp), "CALLS": str(self.tmp / "calls"), "CPU_MODEL": cpu,
                "HPCS_CONF_BAK_DIR": str(self.tmp / "bak"), "HPCS_TMP": str(self.tmp / "tmp"),
                "HPCS_DEBUG_SHM": str(self.tmp / "shm"),
                "HPCS_AMD_DF": str(self.tmp / "amd_df"),
                "HPCS_CMAKE_ROOT": str(self.tmp / "cmake")}
        full.update(env)
        args = ["bash", str(self.mon / "scripts" / SCRIPT.name)] + ([node] if node else [])
        res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, env=full, cwd=str(self.tmp),
                             timeout=60, check=False)
        path = self.tmp / "calls"
        self.calls = path.read_text() if path.exists() else ""
        return res

    def assert_erased(self, res):
        self.assertIn("== erasing debug RPM", res.stdout, res.stdout + res.stderr)
        self.assertIn("systemctl stop hpcperfstats", self.calls)
        self.assertIn("rpm -e hpcperfstatsd", self.calls)

    def assert_kept(self, res, keep_line):
        self.assertIn(keep_line, res.stdout, res.stdout + res.stderr)
        self.assertNotIn("erasing", res.stdout)
        self.assertNotIn("rpm -e", self.calls)
        self.assertNotIn("systemctl stop", self.calls)
        self.assertIn("rpm -q hpcperfstatsd", self.calls)

    def test_unknown_or_missing_node_type_is_a_usage_error(self):
        for node in ("", "frontera"):
            res = self.run_node(node=node)
            self.assertEqual(res.returncode, 2)
            self.assertIn("usage:", res.stderr)
            self.assertEqual(self.calls, "")

    def test_milan_without_gpu_verifies_and_erases(self):
        res = self.run_node()
        out = res.stdout
        self.assertIn("in {}\n".format(self.mon), out)
        self.assertIn("site keys: server queue port user password", out)
        self.assertNotIn("secret", out + res.stderr)
        site = self.mon / "site" / "hpcperfstats.site.conf"
        self.assertEqual(stat.S_IMODE(site.stat().st_mode), 0o600)
        self.assertNotIn("sample_freq", site.read_text())
        self.assertEqual(len(list((self.tmp / "bak").glob("hps-conf.bak.*"))), 1)
        fleet = self.mon / "scripts" / "fleet"
        self.assertEqual(sorted(p.name for p in fleet.iterdir()), ["ls6.force"])
        self.assertIn("git checkout -- src/hpcperfstats.conf hpcperfstats.spec\n"
                      "git pull --ff-only", self.calls)
        self.assertIn("genoa=0", out)
        self.assertIn("== checkout ok\n", out)
        self.assertNotIn("== 2a.", out)
        self.assertNotIn("perf ", self.calls)
        self.assertIn("prep ls6 --debug-build fleet=ls6 allow=\n", self.calls)
        self.assertIn("verify RPM_BUILD=1 checks=onononon", self.calls)
        self.assertIn("== prep rc=0", out)
        self.assertIn("== verify rc=0", out)
        self.assertIn("no NVIDIA GPU: GPU step skipped", out)
        self.assertNotIn("tester ", self.calls)
        self.assert_erased(res)
        self.assertTrue((self.tmp / "tmp" / "hps-cc-c317-001" / "verify.log").exists())

    def test_milan_does_not_need_the_genoa_commit(self):
        (self.mon / "src" / "amd_x86_uncore_df.c").write_text("/* zen3 only */\n")
        self.assert_erased(self.run_node())

    def test_genoa_runs_the_df_step_and_erases(self):
        res = self.run_node(cpu=GENOA)
        self.assertIn("genoa=1", res.stdout)
        self.assertIn("== 2a. DF channels 0-11 under socket-0 read load", res.stdout)
        self.assertIn("socket0 cpus=2", res.stdout)
        perf = [ln for ln in self.calls.splitlines() if ln.startswith("perf ")]
        self.assertEqual(len(perf), 2)
        self.assertEqual(perf[0].count("-e amd_df/event="), 12)
        self.assertIn("event=0x2df,umask=0xfff/", perf[1])
        self.assert_erased(res)

    def test_genoa_gate_stops_without_the_df_commit(self):
        (self.mon / "src" / "amd_x86_uncore_df.c").write_text("/* zen3 only */\n")
        res = self.run_node(cpu=GENOA)
        self.assertIn("== STOP: checkout too old (Genoa needs its commit)", res.stdout)
        self.assertNotIn("prep ", self.calls)
        self.assertNotIn("site_conf", self.calls)
        self.assertIn("NOTE localhost broker: no DB rows", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=x gpu=x df=0")

    def test_genoa_without_canary_keeps_the_rpm(self):
        (self.mon / "scripts" / "canary_gemm_load.sh").unlink()
        res = self.run_node(cpu=GENOA)
        self.assertNotIn("== 2a.", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=0 gpu=0 df=x")

    def test_missing_shared_emit_commit_stops_both_node_types(self):
        (self.mon / "src" / "job_shared_emit.c").write_text("/* old */\n")
        res = self.run_node()
        self.assertIn("== STOP: checkout too old", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=x gpu=x df=0")
        res = self.run_node(node="gb200")
        self.assertIn("== STOP: GB200 peak change not pulled yet", res.stdout)
        self.assertNotIn("prep ", self.calls)

    def test_gpu_present_runs_four_loads_and_erases(self):
        res = self.run_node(NGPU="2")
        out = res.stdout
        self.assertIn("gpus=2", out)
        self.assertIn("== idle\n@fast ts 100.0\ndev 0 sm_active=5\n", out)
        self.assertIn("@full ts 100.0\ndev 0 sm_active=5 fp64=7\n", out)
        loads = [ln.split("-t ")[1].split()[0] for ln in self.calls.splitlines()
                 if ln.startswith("tester --no-dcgm-validation")]
        self.assertEqual(loads, ["1006", "1007", "1008", "1004"])
        self.assertEqual(out.count("tester rc=0"), 4)
        self.assertIn("== idle after", out)
        self.assert_erased(res)

    def test_snap_waits_for_a_mirror_the_restart_removed(self):
        # c122-018 2026-11-24: right after the verify, fast was missing for 89 s (the daemon
        # unlinks it at startup) and the first snap read failed.
        late = self.tmp / "late_fast"
        (self.tmp / "shm" / "fast").rename(late)
        res = self.run_node(NGPU="1", LATE_SRC=str(late))
        self.assertIn("== idle\n@fast ts 100.0\ndev 0 sm_active=5\n", res.stdout,
                      res.stdout + res.stderr)
        self.assertNotIn("shm/fast", res.stderr)
        self.assertEqual(self.calls.count("sleep 1\n"), 3)

    def test_snap_gives_up_on_a_missing_mirror_after_60_polls(self):
        (self.tmp / "shm" / "fast").unlink()
        res = self.run_node(NGPU="1")
        self.assertIn("== idle after", res.stdout)
        self.assertIn("shm/fast", res.stderr)
        polls = self.calls.count("sleep 1\n")
        self.assertTrue(polls > 0 and polls % 60 == 0, polls)
        self.assert_erased(res)

    def test_gpu_present_without_tester_keeps_the_rpm(self):
        (self.tmp / "stubs" / "dcgmproftester13").unlink()
        res = self.run_node(NGPU="1")
        self.assertIn("STOP no dcgmproftester", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=0 gpu=x df=0")

    def test_gpu_present_without_debug_shm_keeps_the_rpm(self):
        (self.tmp / "shm" / "schema").unlink()
        res = self.run_node(NGPU="1")
        self.assertIn("STOP no debug shm", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=0 gpu=x df=0")

    def test_failed_verify_keeps_the_rpm(self):
        res = self.run_node(VERIFY_RC="1")
        self.assertIn("== verify rc=1", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=1 gpu=0 df=0")

    def test_failed_prepare_skips_verify_and_keeps_the_rpm(self):
        res = self.run_node(PREP_RC="1")
        self.assertIn("== prep rc=1", res.stdout)
        self.assertNotIn("verify RPM_BUILD", self.calls)
        self.assertNotIn("== 3.", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=x gpu=x df=0")

    def test_failed_fleet_guard_allows_the_default_broker(self):
        res = self.run_node(GUARD_RC="1")
        self.assertIn("NOTE localhost broker: no DB rows", res.stdout)
        self.assertIn("prep ls6 --debug-build fleet=ls6 allow=1", self.calls)

    def test_correctness_json_is_summarized(self):
        res = self.run_node(WRITE_JSON=JSON)
        self.assertIn("PASS {'PASS': 1} x86perf", res.stdout)
        self.assertIn("PASS     memory_bw        1.000", res.stdout)

    def test_gb200_verifies_with_checks_off_then_tensor_load(self):
        res = self.run_node(node="gb200")
        out = res.stdout
        self.assertIn("== checkout ok (GB200 peaks)", out)
        self.assertNotIn("genoa=", out)
        fleet = self.mon / "scripts" / "fleet"
        self.assertEqual(sorted(p.name for p in fleet.iterdir()), ["horizon.force"])
        self.assertIn("prep horizon --debug-build fleet=horizon", self.calls)
        self.assertIn("verify RPM_BUILD=1 checks=0000", self.calls)
        self.assertIn("tester --no-dcgm-validation -t 1004 -d 120", self.calls)
        self.assertIn("tester rc=0", out)
        self.assertIn("== 4. cleanup (only if verify rc=0 and tester rc=0)", out)
        self.assert_erased(res)

    def test_gb200_without_the_peak_row_stops(self):
        (self.mon / "src" / "nvidia_gpu_estimate.c").write_text("  {9, 0, 128},\n")
        res = self.run_node(node="gb200")
        self.assertIn("== STOP: GB200 peak change not pulled yet", res.stdout)
        self.assertNotIn("prep ", self.calls)
        self.assert_kept(res, "== KEEP debug RPM: verify=x tester=x (rerun needs it)")

    def test_gb200_failed_tester_keeps_the_rpm(self):
        res = self.run_node(node="gb200", TESTER_RC="3")
        self.assertIn("tester rc=3", res.stdout)
        self.assert_kept(res, "== KEEP debug RPM: verify=0 tester=3 (rerun needs it)")

    def test_script_is_executable_and_parses(self):
        self.assertTrue(os.access(str(SCRIPT), os.X_OK))
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
