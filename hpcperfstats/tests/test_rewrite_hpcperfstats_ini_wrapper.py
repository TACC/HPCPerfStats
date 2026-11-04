"""scripts/rewrite_hpcperfstats_ini.sh: runs the rewrite in the release image."""

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "scripts" / "rewrite_hpcperfstats_ini.sh"


def _fake_podman(tmp_path, image_exists=True):
  """Put a recording ``podman`` first on PATH; return (env, log path)."""
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  log = tmp_path / "podman.log"
  rc = 0 if image_exists else 1
  (bin_dir / "podman").write_text(
    "#!/usr/bin/env bash\n"
    f'printf "%s\\n" "$*" >> "{log}"\n'
    f'if [ "$1 $2" = "image exists" ]; then exit {rc}; fi\n'
    "exit 0\n"
  )
  (bin_dir / "podman").chmod(0o755)
  env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
  return env, log


def _run(env, *args, cwd=_REPO):
  return subprocess.run(
    ["bash", str(_WRAPPER), *args],
    cwd=cwd,
    env=env,
    capture_output=True,
    text=True,
    check=False,
  )


def _run_line(log):
  return [ln for ln in log.read_text().splitlines() if ln.startswith("run ")]


def test_runs_checkout_script_in_image_with_checkout_mounted(tmp_path):
  env, log = _fake_podman(tmp_path)
  proc = _run(env, "hpcperfstats.ini", "--dry-run")
  assert proc.returncode == 0, proc.stderr
  (cmd,) = _run_line(log)
  assert f"-v {_REPO}:/work" in cmd
  assert "-w /work " in cmd
  assert "--network none" in cmd
  assert "--entrypoint /usr/local/bin/python3" in cmd
  assert "localhost/hpcperfstats:latest" in cmd
  assert cmd.endswith(
    "scripts/rewrite_hpcperfstats_ini.py hpcperfstats.ini --dry-run"
  )


def test_defaults_to_the_checkout_ini_and_honors_image_flag(tmp_path):
  env, log = _fake_podman(tmp_path)
  proc = _run(env, "--image", "localhost/hpcperfstats:new")
  assert proc.returncode == 0, proc.stderr
  (cmd,) = _run_line(log)
  assert "localhost/hpcperfstats:new " in cmd
  assert cmd.endswith("scripts/rewrite_hpcperfstats_ini.py hpcperfstats.ini")


def test_workdir_follows_cwd_inside_the_checkout(tmp_path):
  env, log = _fake_podman(tmp_path)
  proc = _run(env, "x.ini", cwd=_REPO / "docs")
  assert proc.returncode == 0, proc.stderr
  (cmd,) = _run_line(log)
  assert "-w /work/docs " in cmd
  assert "/work/scripts/rewrite_hpcperfstats_ini.py x.ini" in cmd


def test_refuses_cwd_outside_the_checkout(tmp_path):
  env, log = _fake_podman(tmp_path)
  proc = _run(env, "x.ini", cwd=tmp_path)
  assert proc.returncode == 2
  assert "inside the checkout" in proc.stderr
  assert not log.exists() or not _run_line(log)


def test_missing_image_fails_with_build_hint(tmp_path):
  env, log = _fake_podman(tmp_path, image_exists=False)
  proc = _run(env)
  assert proc.returncode == 1
  assert "not found" in proc.stderr and "rebuild" in proc.stderr
  assert not _run_line(log)
