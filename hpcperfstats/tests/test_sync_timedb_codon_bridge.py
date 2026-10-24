"""Fail-closed Codon parse bridge (missing .so → CPython)."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest

from hpcperfstats.dbload.lib import sync_timedb_codon_bridge as bridge
from hpcperfstats.tests.codon_file_session_fake import (
  attach_parse_stats_file_codon_fake,
)


@pytest.fixture(autouse=True)
def _reset_bridge():
  bridge.reset_codon_bridge_for_tests()
  yield
  bridge.reset_codon_bridge_for_tests()


def test_ensure_codon_parse_dir_on_sys_path_idempotent(
  tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Bake dir is prepended once when present (pytest PYTHONPATH overwrite)."""
  d = tmp_path / "codon_parse"
  d.mkdir()
  monkeypatch.setattr(bridge, "CODON_PARSE_DIR", str(d))
  while str(d) in sys.path:
    sys.path.remove(str(d))
  bridge._ensure_codon_parse_dir_on_sys_path()
  assert sys.path[0] == str(d)
  bridge._ensure_codon_parse_dir_on_sys_path()
  assert sys.path.count(str(d)) == 1


def test_benchmark_inner_keeps_codon_parse_on_pythonpath() -> None:
  """loaded48 inner must not drop /opt/hpcperfstats/codon_parse from PYTHONPATH."""
  from pathlib import Path

  text = Path("tests/run_sync_timedb_benchmark_inner.sh").read_text(
    encoding="utf-8"
  )
  assert "/opt/hpcperfstats/codon_parse" in text
  assert 'PYTHONPATH="/opt/hpcperfstats/codon_parse:' in text


def test_loaded48_soak_isolates_codon_in_subprocess() -> None:
  """Parent pytest must not map Codon .so; child hard-exits after drain."""
  from pathlib import Path

  soak = Path("tests/sync_timedb_benchmark/test_loaded48_soak.py").read_text(
    encoding="utf-8"
  )
  child = Path(
    "tests/sync_timedb_benchmark/loaded48_supervisor_child.py"
  ).read_text(encoding="utf-8")
  assert "subprocess.Popen" in soak
  assert "loaded48_supervisor_child" in soak
  assert "os._exit" in child
  assert "shutdown_codon_parse_pool" in child


def test_missing_extension_returns_none(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv(bridge.CODON_FORCE_DISABLE_ENV, "1")
  assert bridge.try_codon_parse_collapse(["1.0 j h"]) is None
  assert bridge.try_codon_feed_collapse_fingerprint(["1.0 j h"]) is None
  assert bridge.codon_parse_available() is False


def test_loaded_extension_fingerprint_path(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = ModuleType("fake_codon_parse")

  def _fp(lines: list[str]) -> tuple[int, int, float, float]:
    assert lines
    return (10, 5, 1.5, 2.0)

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, "fake_codon_parse", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_parse")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()
  out = bridge.try_codon_feed_collapse_fingerprint(["x"])
  assert out == {
    "raw_stats": 10,
    "collapsed_rows": 5,
    "collapsed_value_sum": 1.5,
    "proc_rows": 2,
  }
  # Full parse API absent → None (CPython path)
  assert bridge.try_codon_parse_collapse(["x"]) is None


def test_loaded_extension_parse_stats_lines_codon_path(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = ModuleType("fake_codon_parse_full")

  def _fp(lines: list[str]) -> tuple[int, int, float, float]:
    return (1, 1, 1.0, 0.0)

  def _parse(lines: list[str]) -> tuple[list[dict], list[dict]]:
    assert lines == ["row"]
    return ([{"type": "cpu", "event": "user", "value": 1.0}], [])

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, "fake_codon_parse_full", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_parse_full")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()
  out = bridge.try_codon_parse_collapse(["row"])
  assert out is not None
  stats, procs = out
  assert stats[0]["event"] == "user"
  assert procs == []
  assert bridge.try_codon_feed_batch_parse(["row"]) == out


def test_parse_stats_lines_still_cpython_without_ext() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import parse_stats_lines

  stats, procs = parse_stats_lines(
    [
      "!cpu user,W=64,U=1",
      "1000.0 J1 h1",
      "cpu 0 @full 1.0",
    ],
    0,
  )
  assert isinstance(stats, list)
  assert isinstance(procs, list)


def test_format_codon_parse_startup_line_disabled_by_env(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv(bridge.CODON_FORCE_DISABLE_ENV, "1")
  line = bridge.format_codon_parse_startup_line()
  assert line.startswith("codon_parse status=disabled")
  assert "HPCPERFSTATS_CODON_PARSE_DISABLE" in line
  assert bridge.codon_ingest_parse_enabled() is False


def test_format_codon_parse_startup_line_enabled_with_parse_api(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = ModuleType("fake_codon_parse_startup")

  def _fp(lines: list[str]) -> tuple[int, int, float, float]:
    return (0, 0, 0.0, 0.0)

  def _parse(lines: list[str]) -> tuple[list, list]:
    return ([], [])

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  attach_parse_stats_file_codon_fake(mod, _parse)
  monkeypatch.setitem(sys.modules, "fake_codon_parse_startup", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_parse_startup")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()
  monkeypatch.setattr(
    bridge,
    "configure_codon_context_pool_from_ini",
    lambda: bridge._configure_slot_pool(8),
  )
  bridge._configure_slot_pool(8)
  line = bridge.format_codon_parse_startup_line()
  assert line == (
    "codon_parse status=enabled module=fake_codon_parse_startup "
    "api=parse_stats_lines_codon gil=off contexts=8 "
    "codon_mode=in_process codonrt_serialize=0"
  )
  assert bridge.codon_ingest_parse_enabled() is True
  logged: list[str] = []

  def _capture(msg: str, flush: bool = False) -> None:
    logged.append(msg)

  assert bridge.log_codon_parse_startup(printer=_capture) == line
  assert logged == [line]


def test_sync_timedb_main_logs_codon_parse_startup() -> None:
  """CLI main and in-process supervisor both size Codon; CLI after ABI log."""
  from pathlib import Path

  src = (
    Path(__file__).resolve().parents[2]
    / "hpcperfstats"
    / "dbload"
    / "sync_timedb.py"
  ).read_text(encoding="utf-8")
  assert src.count("log_codon_parse_startup()") >= 2
  abi_idx = src.index("log_python_abi_startup()")
  # CLI path: first codon call after ABI fingerprint.
  codon_after_abi = src.index("log_codon_parse_startup()", abi_idx)
  assert codon_after_abi > abi_idx
  sup_at = src.index("def run_sync_timedb_supervisor_from_parsed(")
  assert "log_codon_parse_startup()" in src[sup_at:abi_idx]


def test_codon_kernel_max_slots_matches_bridge() -> None:
  from pathlib import Path

  capi = (
    Path(__file__).resolve().parents[1]
    / "dbload"
    / "codon"
    / "feed_collapse_capi.py"
  ).read_text(encoding="utf-8")
  pyext = (
    Path(__file__).resolve().parents[1] / "dbload" / "codon" / "pyext_ft_init.c"
  ).read_text(encoding="utf-8")
  assert "MAX_CODON_SLOTS = 128" in capi
  assert "#define MAX_CODON_SLOTS 128" in pyext
  assert bridge.MAX_CODON_SLOTS == 128


def test_configure_codon_slots_equal_ingest_threads(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Slot count is the INI ingest pool, not the legacy fraction."""
  from hpcperfstats.dbload.lib import conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 48)
  monkeypatch.setattr(
    cfg, "get_sync_codon_context_pool_fraction", lambda: 0.125
  )
  n = bridge.configure_codon_context_pool_from_ini()
  assert n == 48
  assert bridge.codon_context_pool_size() == 48
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 200)
  assert (
    bridge.configure_codon_context_pool_from_ini() == bridge.MAX_CODON_SLOTS
  )
  bridge._configure_slot_pool(1)


def test_codon_n_equals_ingest_pool_default() -> None:
  """Default fraction 1.0 maps ingest pool width P to N Codon workers."""
  assert bridge.DEFAULT_CONTEXT_POOL_FRACTION == 1.0
  assert bridge.MAX_CODON_SLOTS == 128
  assert bridge.derive_codon_context_pool_size(64) == 64
  assert bridge.derive_codon_context_pool_size(48, 1.0) == 48


def test_derive_codon_context_pool_size_fraction() -> None:
  assert bridge.derive_codon_context_pool_size(64, 0.5) == 32
  assert bridge.derive_codon_context_pool_size(64) == 64
  assert bridge.derive_codon_context_pool_size(64, 1.0) == 64
  assert bridge.derive_codon_context_pool_size(64, 0.125) == 8
  assert bridge.derive_codon_context_pool_size(1, 0.125) == 1
  assert bridge.derive_codon_context_pool_size(96, 0.125) == 12


def test_codon_ipc_binary_roundtrip_no_pickle() -> None:
  """Parent/child framing uses UTF-8 + JSON/struct; no pickle/msgpack."""
  from pathlib import Path

  from hpcperfstats.dbload.lib import codon_process_worker as worker

  for path in (
    Path(bridge.__file__),
    Path(worker.__file__),
  ):
    text = path.read_text(encoding="utf-8")
    assert "import pickle" not in text
    assert "pickle." not in text
    assert "import msgpack" not in text
  assert worker.PROTO_VER == 4
  req = worker.encode_request("parse", ["a", "b"])
  assert worker.decode_request(req) == (
    "parse",
    {"lines": ["a", "b"], "carry_raw": {}, "carry_arc": {}},
  )
  fp_frame = worker.encode_response_ok("fp", (10, 5, 1.5, 2.0))
  assert worker.decode_response("fp", fp_frame) == (10, 5, 1.5, 2.0)
  parse_body = {
    "stats": [{"event": "user", "value": 1.0, "host": "h", "time": 1.0}],
    "procs": [{"proc": "x"}],
    "marks": [],
    "carry_raw": {},
    "carry_arc": {},
    "finished": True,
  }
  parse_frame = worker.encode_response_ok("parse", parse_body)
  decoded = worker.decode_response("parse", parse_frame)
  assert decoded["stats"]["event"] == ["user"]
  assert decoded["stats"]["value"] == [1.0]
  assert decoded["procs"]["proc"] == ["x"]
  assert decoded["finished"] is True
  err = worker.encode_response_err("boom")
  try:
    worker.decode_response("parse", err)
  except RuntimeError as exc:
    assert "boom" in str(exc)
  else:
    raise AssertionError("expected RuntimeError")


def test_codon_process_worker_proto_v3_soa_carry_roundtrip() -> None:
  """PROTO_VER 3 parse response round-trips SoA stats + carry JSON sidecar."""
  from hpcperfstats.dbload.lib import codon_process_worker as worker

  carry_raw = {"h\x00cpu\x000\x00user": [5.0, 4.0, 1.0, 102.0]}
  carry_arc = {"h\x00cpu\x000\x00user": 103.0}
  req = worker.encode_request("parse", ["a", "b"], carry_raw, carry_arc)
  assert req[:2] == bytes([worker.PROTO_VER, worker.KIND_PARSE])
  kind, envelope = worker.decode_request(req)
  assert kind == "parse"
  assert envelope == {
    "lines": ["a", "b"],
    "carry_raw": carry_raw,
    "carry_arc": carry_arc,
  }

  body = {
    "stats": [
      {
        "time": 1.0,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.5,
        "arc": 0.1,
      }
    ],
    "procs": [],
    "marks": [{"kind": "affinity", "jid": "9"}],
    "carry_raw": carry_raw,
    "carry_arc": carry_arc,
    "finished": True,
  }
  frame = worker.encode_response_ok("parse", body)
  decoded = worker.decode_response("parse", frame)
  assert decoded["stats"]["event"] == ["user"]
  assert decoded["stats"]["delta"] == [0.5]
  assert decoded["carry_raw"] == carry_raw
  assert decoded["carry_arc"] == carry_arc
  assert decoded["marks"][0]["jid"] == "9"
  assert decoded["finished"] is True

  # fp frames still use a plain lines blob under the bumped PROTO_VER.
  fp_req = worker.encode_request("fp", ["x", "y"])
  assert fp_req[:2] == bytes([worker.PROTO_VER, worker.KIND_FP])
  assert worker.decode_request(fp_req) == ("fp", ["x", "y"])


def test_codon_bridge_skip_finished(monkeypatch: pytest.MonkeyPatch) -> None:
  """Finished-contract dict result updates carry and sets codon_finished_chunk."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  mod = ModuleType("fake_codon_finished")

  def _fp(lines: list[str]) -> tuple[int, int, float, float]:
    return (1, 1, 1.0, 0.0)

  def _parse(
    lines: list[str],
    slot: int = 0,
    carry_raw: dict | None = None,
    carry_arc: dict | None = None,
  ) -> dict:
    assert lines == ["row"]
    assert carry_raw == {}
    assert carry_arc == {}
    return {
      "stats": [
        {"type": "cpu", "event": "user", "value": 1.0, "delta": 0.5, "arc": 0.1}
      ],
      "procs": [],
      "carry_raw": {"k": [1.0, 4.0, 1.0, 100.0]},
      "carry_arc": {"k": 100.0},
      "finished": True,
    }

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, "fake_codon_finished", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_finished")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()

  carry = DeltaCarryState()
  out = bridge.try_codon_parse_collapse(["row"], carry=carry)
  assert out is not None
  stats, procs = out
  assert stats[0]["delta"] == 0.5
  assert stats[0]["arc"] == 0.1
  assert procs == []
  assert carry.raw == {"k": [1.0, 4.0, 1.0, 100.0]}
  assert carry.arc == {"k": 100.0}
  assert carry.codon_finished_chunk is True


def test_codon_no_silent_cpython_fallback(
  tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """codon_primary + injected failure must raise; Incremental not invoked."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    parse_stats_file_streaming_incremental,
  )

  monkeypatch.setattr(bridge, "codon_ingest_parse_enabled", lambda: True)

  incremental_calls: list[list[str]] = []
  orig_feed_lines = IncrementalStatsParser.feed_lines

  def _spy_feed_lines(self, lines):
    incremental_calls.append(list(lines))
    return orig_feed_lines(self, lines)

  monkeypatch.setattr(IncrementalStatsParser, "feed_lines", _spy_feed_lines)

  def _boom_file(
    path: str,
    flush_b: int,
    carry: Any,
    on_chunk: Any,
  ) -> None:
    raise RuntimeError("codon worker exploded")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_codon_bridge.try_codon_parse_file_stream",
    _boom_file,
  )
  stats_path = tmp_path / "host"
  stats_path.write_text(
    "!cpu user,W=64,U=1\n1000.0 J1 h1\ncpu 0 @full 1.0\n",
    encoding="utf-8",
  )
  with pytest.raises(RuntimeError, match="codon worker exploded"):
    parse_stats_file_streaming_incremental(
      str(stats_path),
      flush_rows=1,
      on_chunk=lambda s, p: None,
    )
  assert not incremental_calls, (
    "IncrementalStatsParser must not run when codon_primary is enabled"
  )


def test_streaming_incremental_diverts_flush_to_codon(
  tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Codon-primary streaming uses file-owned parse (not parent line batches)."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    parse_stats_file_streaming_incremental,
  )

  calls: list[list[str]] = []
  mod = ModuleType("fake_codon_stream_divert")

  def _fp(lines: list[str], slot: int = 0) -> tuple[int, int, float, float]:
    return (1, 1, 1.0, 0.0)

  def _parse(
    lines: list[str],
    slot: int = 0,
    carry_raw: dict | None = None,
    carry_arc: dict | None = None,
  ) -> dict:
    calls.append(list(lines))
    return {
      "stats": [
        {"type": "cpu", "event": "user", "value": 1.0, "time": 1000.0},
      ],
      "procs": [],
      "carry_raw": dict(carry_raw or {}),
      "carry_arc": dict(carry_arc or {}),
      "finished": True,
    }

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  attach_parse_stats_file_codon_fake(mod, _parse)
  monkeypatch.setitem(sys.modules, "fake_codon_stream_divert", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_stream_divert")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()

  def _parse_file(
    path: str,
    flush_b: int,
    carry: Any,
    on_chunk: Any,
  ) -> None:
    from hpcperfstats.dbload.lib.codon_process_worker import (
      codon_worker_parse_file_frames,
    )

    for frame in codon_worker_parse_file_frames(path, flush_b):
      stats, procs = frame.get("stats"), frame.get("procs")
      on_chunk(stats, procs)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_codon_bridge.try_codon_parse_file_stream",
    _parse_file,
  )
  stats_path = tmp_path / "host"
  stats_path.write_text(
    "!cpu user,W=64,U=1\n"
    "1000.0 J1 h1\n"
    "cpu 0 @full 1.0\n"
    "1001.0 J1 h1\n"
    "cpu 0 @full 2.0\n",
    encoding="utf-8",
  )
  chunks: list[Any] = []
  parse_stats_file_streaming_incremental(
    str(stats_path),
    flush_rows=1,
    on_chunk=lambda s, p: chunks.append((s, p)),
  )
  assert chunks, "expected Codon file-owned divert on streaming ingest"


def test_codonrt_serialize_default_off() -> None:
  """Process-wide codonrt serialize is removed; stripes only."""
  assert not hasattr(bridge, "_CODON_RT_SERIALIZE")
  assert not hasattr(bridge, "_codon_rt_lock")
  line = bridge.format_codon_parse_startup_line()
  # Fake/unavailable may disable; when enabled must show serialize=0.
  if "status=enabled" in line:
    assert "codonrt_serialize=0" in line
    assert "codon_mode=" in line


def test_use_codon_process_pool_false_with_ext_env(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Host fakes must stay in-process (CODON_EXT_ENV set)."""
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_mod")
  monkeypatch.setattr(bridge, "_is_free_threaded_build", lambda: True)
  monkeypatch.setattr(bridge.os.path, "isfile", lambda _p: True)
  assert bridge._use_codon_process_pool() is False


def test_gil_enabled_after_import_fail_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = ModuleType("fake_codon_gil_on")

  def _fp(lines: list[str]) -> tuple[int, int, float, float]:
    return (0, 0, 0.0, 0.0)

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, "fake_codon_gil_on", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_gil_on")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  monkeypatch.setattr(bridge, "_is_free_threaded_build", lambda: True)
  monkeypatch.setattr(bridge, "_is_gil_enabled", lambda: True)
  bridge.reset_codon_bridge_for_tests()
  assert bridge.codon_parse_available() is False
  line = bridge.format_codon_parse_startup_line()
  assert "gil_enabled_after_import" in line or "extension_unavailable" in line


def test_slot_lock_held_only_during_native_call(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  mod = ModuleType("fake_codon_slot")
  held_during: list[int] = []

  def _parse(lines: list[str], slot: int = 0) -> tuple[list, list]:
    held, n = bridge.codon_slot_occupancy()
    held_during.append(held)
    assert n >= 1
    assert held >= 1
    return ([], [])

  def _fp(lines: list[str], slot: int = 0) -> tuple[int, int, float, float]:
    return (0, 0, 0.0, 0.0)

  mod.feed_and_collapse_fingerprint = _fp  # type: ignore[attr-defined]
  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  attach_parse_stats_file_codon_fake(mod, _parse)
  monkeypatch.setitem(sys.modules, "fake_codon_slot", mod)
  monkeypatch.setenv(bridge.CODON_EXT_ENV, "fake_codon_slot")
  monkeypatch.delenv(bridge.CODON_FORCE_DISABLE_ENV, raising=False)
  bridge.reset_codon_bridge_for_tests()
  bridge._configure_slot_pool(4)
  assert bridge.try_codon_parse_collapse(["x"]) == ([], [])
  assert held_during and held_during[0] >= 1
  held, n = bridge.codon_slot_occupancy()
  assert held == 0
  assert n == 4
  assert "codon_slot_occ=0/4" in bridge.format_codon_slot_occ_census_token()


def test_pyext_source_declares_gil_not_used() -> None:
  from pathlib import Path

  src = (
    Path(__file__).resolve().parents[2]
    / "hpcperfstats"
    / "dbload"
    / "codon"
    / "pyext_ft_init.c"
  ).read_text(encoding="utf-8")
  assert "Py_MOD_GIL_NOT_USED" in src
  assert "Py_MOD_GIL_USED" not in src


def test_codon_copy_unique_on_carry(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Finished-contract dict sets carry.codon_copy_unique from frame sidecar."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  carry = DeltaCarryState()
  result = {
    "stats": {"time": [1.0], "host": ["h"]},
    "procs": {"proc": []},
    "carry_raw": {},
    "carry_arc": {},
    "finished": True,
    "copy_unique": True,
  }
  bridge._finish_codon_parse_result(result, carry)
  assert carry.codon_finished_chunk is True
  assert carry.codon_copy_unique is True


def test_marshaller_pipelines_two_frames(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Codon-hit submits use non-blocking put until depth-2 then join at EOF."""
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib.sync_timedb_ingest_write_stage import (
    IngestWritePayload,
  )

  submitted: list[IngestWritePayload] = []

  def _fake_submit(payload: IngestWritePayload) -> IngestWritePayload:
    submitted.append(payload)
    payload.done.set()
    payload.ingest_ok = True
    return payload

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_ingest_write_stage."
    "ingest_write_stage_started",
    lambda: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_ingest_write_stage."
    "submit_ingest_write",
    _fake_submit,
  )
  outstanding: list[IngestWritePayload] = []
  for _ in range(3):
    st._submit_stats_payload_write(
      "/f",
      {"time": [1.0]},
      {},
      pipeline_depth=2,
      outstanding=outstanding,
      copy_unique=True,
    )
  assert len(submitted) == 3
  assert len(outstanding) == 1
  archival, ok = st._drain_ingest_write_pipeline(outstanding)
  assert ok and archival
  assert not outstanding


def test_finalize_codon_parse_copy_emit_sets_copy_unique() -> None:
  from hpcperfstats.dbload.lib.codon_process_worker import (
    _finalize_codon_parse_copy_emit,
  )
  from hpcperfstats.tests.test_codon_marks_parity import _load_feed_collapse

  mod = _load_feed_collapse()
  finished = (
    [1.0, 1.0],
    ["h", "h"],
    ["1", "1"],
    ["cpu", "cpu"],
    ["", ""],
    ["user", "user"],
    ["%", "%"],
    [1.0, 2.0],
    [0.0, 0.0],
    [0.0, 0.0],
  )
  proc = (
    ["1", "1"],
    ["h", "h"],
    ["p", "p"],
    [{"vm_stk": 9, "threads": 1}, {"vm_stk": 3, "threads": 8}],
    ["p/1", "p/2"],
  )
  finished, proc = mod.finalize_copy_unique_emit(finished, proc)
  assert len(finished[0]) == 1
  assert finished[7][0] == 2.0
  assert len(proc[2]) == 1
  assert proc[3][0]["vm_stk"] == 9
  assert proc[3][0]["threads"] == 8
  body = {
    "stats": dict(
      zip(
        (
          "time",
          "host",
          "jid",
          "type",
          "dev",
          "event",
          "unit",
          "value",
          "delta",
          "arc",
        ),
        finished,
        strict=True,
      )
    ),
    "procs": {},
  }
  out = _finalize_codon_parse_copy_emit(body)
  assert out["copy_unique"] is True
  already = {
    "stats": {"time": [1.0]},
    "procs": {"proc": ["p"]},
    "copy_unique": True,
  }
  assert _finalize_codon_parse_copy_emit(already) is already


def test_codon_ipc_normalizes_scalars_before_wire() -> None:
  """COPY cell coercion runs in finalize before SoA IPC encode/decode."""
  from datetime import datetime

  from hpcperfstats.dbload.lib import (
    codon_process_worker as worker,
    sync_timedb_host_data_insert as hdi,
  )

  raw = {
    "time": [1_700_000_000.0],
    "host": ["h"],
    "jid": ["-"],
    "type": ["cpu"],
    "dev": [float("nan")],
    "event": ["user"],
    "unit": ["%"],
    "value": [float("nan")],
    "delta": [1.5],
    "arc": [0.0],
  }
  body = worker._finalize_codon_parse_copy_emit({"stats": raw, "procs": {}})
  assert body["stats"]["jid"] == [None]
  assert body["stats"]["dev"] == [""]
  assert body["stats"]["value"] == [None]
  assert isinstance(body["stats"]["time"][0], datetime)
  frame = worker.encode_response_ok(
    "parse",
    {
      "stats": body["stats"],
      "procs": body["procs"],
      "marks": [],
      "carry_raw": {},
      "carry_arc": {},
      "finished": True,
      "copy_unique": True,
    },
  )
  decoded = worker.decode_response("parse", frame)
  assert decoded["stats"]["jid"] == [None]
  assert decoded["stats"]["value"] == [None]
  assert isinstance(decoded["stats"]["time"][0], datetime)
  copy_bytes = hdi.host_rows_to_copy_bytes(decoded["stats"], copy_unique=True)
  assert b"\\N" in copy_bytes
  assert b"\t-\t" not in copy_bytes


def test_divert_off_still_incremental_when_emission_start(
  tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """emission_start>0 never enters Codon-primary file parse."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    parse_stats_file_streaming_incremental,
  )

  calls: list[str] = []

  def _boom(*_a, **_k):
    calls.append("codon")
    raise AssertionError("codon must not run")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_codon_bridge."
    "try_codon_parse_file_stream",
    _boom,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_codon_bridge."
    "codon_ingest_parse_enabled",
    lambda: True,
  )
  stats_path = tmp_path / "host"
  stats_path.write_text(
    "!cpu user,W=64,U=1\n1000.0 J1 h1\ncpu 0 @full 1.0\n",
    encoding="utf-8",
  )
  chunks: list[Any] = []
  parse_stats_file_streaming_incremental(
    str(stats_path),
    start_line_idx=1,
    flush_rows=1,
    on_chunk=lambda s, p: chunks.append((s, p)),
  )
  assert not calls
  assert chunks


def test_worker_parse_file_frames_requires_kernel_file_api(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Divert-on file worker must not fall back to CPython line batching."""
  from hpcperfstats.dbload.lib import codon_process_worker as worker

  mod = ModuleType("fake_codon_no_file_api")

  def _parse(lines: list[str], slot: int = 0) -> dict:
    return {"stats": {}, "procs": {}, "finished": True}

  mod.parse_stats_lines_codon = _parse  # type: ignore[attr-defined]
  monkeypatch.setitem(sys.modules, "fake_codon_no_file_api", mod)
  monkeypatch.setenv(worker.CODON_EXT_ENV, "fake_codon_no_file_api")
  with pytest.raises(RuntimeError, match="parse_stats_file_codon_open"):
    list(worker.codon_worker_parse_file_frames(__file__, 1024))


def test_worker_source_does_not_import_codon_file_bundles() -> None:
  from pathlib import Path

  src = Path("hpcperfstats/dbload/lib/codon_process_worker.py").read_text(
    encoding="utf-8"
  )
  assert "codon_file_bundles" not in src


def test_proto4_parse_file_request_no_lines() -> None:
  """PROTO 4 file parse request must not include a lines array."""
  from hpcperfstats.dbload.lib import codon_process_worker as worker

  frame = worker.encode_parse_file_request("/stats/path", 1048576, 0)
  assert frame[:2] == bytes([worker.PROTO_VER, worker.KIND_PARSE_FILE])
  kind, envelope = worker.decode_request(frame)
  assert kind == "parse_file"
  assert "lines" not in envelope
  assert envelope["path"] == "/stats/path"
  assert envelope["flush_max_bytes"] == 1048576
  assert "carry_raw" not in envelope
  assert "carry_arc" not in envelope


def test_parse_file_ipc_sidecar_omits_carry() -> None:
  """File-session frames export SoA + COPY meta only (no carry on wire)."""
  from hpcperfstats.dbload.lib import codon_process_worker as worker

  body = worker._ipc_frame_from_parsed(
    {
      "stats": {"time": []},
      "procs": {},
      "marks": [],
      "carry_raw": {"k": [1.0, 2.0, 1.0, 3.0]},
      "carry_arc": {"k": 3.0},
      "finished": True,
      "copy_unique": True,
    },
    eof=True,
  )
  frame = worker.encode_response_ok("parse", body)
  decoded = worker.decode_response("parse", frame)
  assert decoded.get("carry_raw", {}) == {}
  assert decoded.get("carry_arc", {}) == {}
  assert decoded["copy_unique"] is True
  assert decoded["eof"] is True


def test_bake_script_requires_gil_off_smoke() -> None:
  from pathlib import Path

  text = (
    Path(__file__).resolve().parents[2]
    / "hpcperfstats"
    / "dbload"
    / "codon"
    / "build_codon_ext.sh"
  ).read_text(encoding="utf-8")
  assert "codon_pyext_314t_gil_off=pass" in text
  assert "Py_MOD_GIL_NOT_USED" in text or "GIL_NOT_USED" in (
    Path(__file__).resolve().parents[2]
    / "hpcperfstats"
    / "dbload"
    / "codon"
    / "pyext_ft_init.c"
  ).read_text(encoding="utf-8")
