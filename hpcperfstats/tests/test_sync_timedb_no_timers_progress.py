"""Regression: sync_timedb progress + idle kill; no internal wall soft-kill."""

from __future__ import annotations

import inspect
import signal
import subprocess

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import multiprocessing_pool_health as mph
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout
from hpcperfstats.dbload.lib import sync_timedb_progress_io as progress_io


def test_resolve_timeout_always_zero_cannot_rearm(monkeypatch, tmp_path):
  """Wall soft-kill cannot be re-armed via size helpers even if floor patched."""
  monkeypatch.setattr(
      ingest_timeout.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 9999.0,
  )
  monkeypatch.setattr(
      ingest_timeout.cfg,
      "get_sync_ingest_per_file_timeout_s_per_mib",
      lambda: 99.0,
  )
  monkeypatch.setattr(
      ingest_timeout.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 99999.0,
  )
  stats = tmp_path / "a"
  stats.write_bytes(b"x" * 1024)
  assert ingest_timeout.resolve_ingest_per_file_timeout_s(str(stats)) == 0.0
  assert ingest_timeout.resolve_ingest_per_file_timeout_for_size_bytes(1 << 40) == 0.0
  assert (
      ingest_timeout.max_ingest_per_file_timeout_for_paths([str(stats)]) == 0.0
  )
  monkeypatch.undo()
  assert cfg.get_sync_ingest_per_file_timeout_s() == 0.0
  assert cfg.get_sync_ingest_per_file_timeout_s_per_mib() == 0.0
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 0


def test_stall_abort_polls_disabled_pool_reclaim():
  """Poll-count stall abort is deleted (0); recover has no exit-124 wall."""
  assert ingest_timeout.stall_abort_polls_for_paths(["/a"]) == 0
  assert (
      ingest_timeout.stall_abort_polls_for_sealed_archives(["/a.tar.zst"]) == 0
  )
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 0
  src = inspect.getsource(mph)
  assert "idle pool recover exceeded wall_s" not in src
  assert "recover_thread.join()" in src


def test_run_ingest_timed_idle_only_no_sigalrm(monkeypatch, tmp_path):
  """_run_ingest_timed must not arm wall SIGALRM / setitimer."""
  stats = tmp_path / "host.job"
  stats.write_text("x")
  armed = {"n": 0}

  def _boom(*_a, **_k):
    armed["n"] += 1
    raise AssertionError("setitimer must not arm for ingest wall")

  if hasattr(signal, "setitimer"):
    monkeypatch.setattr(signal, "setitimer", _boom)
  monkeypatch.setattr(cfg, "get_sync_ingest_stall_idle_s", lambda: 1800.0)
  assert st._run_ingest_timed(str(stats), "test", lambda: "ok") == "ok"
  assert armed["n"] == 0


def test_alive_alone_does_not_reset_idle(tmp_path):
  """Process liveness alone must not count as semantic progress."""
  marker = tmp_path / "out.bin"
  marker.write_bytes(b"")
  with pytest.raises(progress_io.ProgressIdleError):
    progress_io.run_subprocess_with_progress(
        ["sleep", "30"],
        progress_path=str(marker),
        stage="test_alive",
        metric="bytes",
        idle_s=0.4,
        poll_s=0.1,
    )


def test_byte_progress_resets_idle(tmp_path):
  """Growing progress_path allows complete past idle window."""
  out = tmp_path / "grow.bin"
  out.write_bytes(b"")
  script = tmp_path / "grow.py"
  script.write_text(
      "import time, pathlib\n"
      "p = pathlib.Path(%r)\n"
      "for i in range(5):\n"
      "  p.write_bytes(b'x' * (i + 1))\n"
      "  time.sleep(0.25)\n"
      % str(out),
  )
  result = progress_io.run_subprocess_with_progress(
      ["python3", str(script)],
      progress_path=str(out),
      stage="test_grow",
      metric="bytes",
      idle_s=0.8,
      poll_s=0.1,
  )
  assert result.returncode == 0


def test_append_to_tar_uses_progress_helper_not_timeout_3600():
  """Tar append must use progress+idle kill; no absolute 3600 wall."""
  src = inspect.getsource(st._append_to_tar)
  assert "run_subprocess_with_progress" in src
  assert "timeout=3600" not in src
  assert "tar_timeout_s" not in src


def test_append_to_tar_idle_kill(monkeypatch, tmp_path):
  """Hung tar append with flat size raises RuntimeError idle stall."""
  tar_path = tmp_path / "day.tar"
  subprocess.run(
      ["tar", "cf", str(tar_path), "-T", "/dev/null"],
      check=True,
      capture_output=True,
  )
  member = tmp_path / "raw"
  member.write_text("payload")

  def _idle(*_a, **_k):
    raise progress_io.ProgressIdleError("idle", idle_s=1.0, path=str(tar_path))

  import hpcperfstats.dbload.lib.sync_timedb_progress_io as pio

  monkeypatch.setattr(pio, "run_subprocess_with_progress", _idle)
  with pytest.raises(RuntimeError, match="idle stall"):
    st._append_to_tar(str(tar_path), [str(member)])


def test_progress_sop_log_shape(capsys):
  """SOP log lines must include advancing=true|false and metric=."""
  progress_io.log_progress_sop(
      stage="tar_append",
      path="/daily/x.tar",
      advancing=False,
      idle_s=12.5,
      last_progress=1.0,
      metric="bytes",
      force=True,
  )
  out = capsys.readouterr().out
  assert "progress stage=tar_append" in out
  assert "advancing=false" in out
  assert "metric=bytes" in out


def test_arch_no_internal_wall_timers_append_and_ingest():
  """Architecture: no wall soft-kill on tar append / ingest timed bodies."""
  append_src = inspect.getsource(st._append_to_tar)
  timed_src = inspect.getsource(st._run_ingest_timed)
  assert "timeout=3600" not in append_src
  assert "setitimer" not in timed_src
  assert "signal.alarm" not in timed_src
  assert "signal.signal" not in timed_src
  assert ingest_timeout.stall_abort_polls_for_paths(["x"]) == 0
