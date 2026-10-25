"""loaded48 soak supervisor child — owns Codon; exits via ``os._exit``.

Parent pytest must not import ``hpcperfstats_codon_parse``. Parent SIGTERMs this
process at the soak deadline; cooperative handlers request shutdown. After the
supervisor returns, kill Codon Popen workers and ``os._exit`` so FT interpreter
teardown never runs with Codon mapped in the pytest process.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def main() -> int:
  """
  Run ``run_sync_timedb_supervisor_from_parsed`` then hard-exit.

  Env:
    HPCPERFSTATS_LOADED48_INGEST_WIDTH: monkeypatch ingest pool width.
    HPCPERFSTATS_LOADED48_OCC_PATH: optional JSON path for occupancy samples.
    HPCPERFSTATS_LOADED48_DONE_PATH: optional marker written before ``os._exit``.

  Returns:
    int: Unused — process ends via ``os._exit``.
  """
  width_raw = os.environ.get("HPCPERFSTATS_LOADED48_INGEST_WIDTH", "").strip()
  occ_path = os.environ.get("HPCPERFSTATS_LOADED48_OCC_PATH", "").strip()
  done_path = os.environ.get("HPCPERFSTATS_LOADED48_DONE_PATH", "").strip()

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as orch
  from hpcperfstats.dbload.sync_timedb import (
    database_startup,
    run_sync_timedb_supervisor_from_parsed,
  )

  if width_raw:
    width = int(width_raw)
    cfg.get_sync_ingest_pool_processes = (  # type: ignore[method-assign]
      lambda: width
    )

  from tests.sync_timedb_benchmark.screening_runner import (
    configure_loaded48_continuous_discover,
    enable_stack_dumps,
    install_gc_probe,
    loaded48_conf_overrides,
  )

  configure_loaded48_continuous_discover(orch)
  # Test-only INI-getter overrides (HPCPERFSTATS_LOADED48_MALLOC_TRIM=no, ...).
  for _name, _fn in loaded48_conf_overrides(os.environ).items():
    setattr(cfg, _name, _fn)
    print("loaded48 conf override %s -> %r" % (_name, _fn()), flush=True)
  # Opt-in GC pause probe: HPCPERFSTATS_LOADED48_GC_MODE=trace|off (test hosts only).
  _gc_probe = install_gc_probe(os.environ.get("HPCPERFSTATS_LOADED48_GC_MODE", ""))
  if _gc_probe is not None:
    import threading

    def _gc_report() -> None:
      while True:
        time.sleep(30.0)
        print(_gc_probe.format_line(), flush=True)

    print(_gc_probe.format_line(), flush=True)
    threading.Thread(target=_gc_report, name="gc-probe", daemon=True).start()
  # Opt-in: periodic all-thread stack dumps to show where threads are parked.
  _stack_fh = enable_stack_dumps(
    os.environ.get("HPCPERFSTATS_LOADED48_STACKDUMP_S", ""),
    os.environ.get("HPCPERFSTATS_LOADED48_STACKDUMP_PATH", ""),
  )

  occupancy_samples: list[list[float | int]] = []
  t0 = time.monotonic()
  last_occ_flush = 0.0
  if occ_path:
    orig_fill = orch._ingest_coordinator_fill_tick
    orig_drain = orch._drain_ingest_ready

    def _flush_occ(force: bool = False) -> None:
      nonlocal last_occ_flush
      now = time.monotonic()
      if not force and (now - last_occ_flush) < 2.0:
        return
      last_occ_flush = now
      tmp = occ_path + ".tmp"
      Path(tmp).write_text(json.dumps(occupancy_samples), encoding="utf-8")
      os.replace(tmp, occ_path)

    def _fill_tick_with_occ(*args, **kwargs):
      result = orig_fill(*args, **kwargs)
      inflight = kwargs.get("ingest_inflight")
      if inflight is None and len(args) >= 6:
        inflight = args[5]
      n = len(inflight) if isinstance(inflight, dict) else 0
      submitted = int(result[0]) if result else 0
      zcard = int(result[2]) if result and len(result) > 2 else 0
      occupancy_samples.append(
        [time.monotonic() - t0, int(n), int(zcard), int(submitted)]
      )
      _flush_occ()
      return result

    def _drain_with_occ(*args, **kwargs):
      inflight = kwargs.get("inflight")
      n = len(inflight) if isinstance(inflight, dict) else 0
      occupancy_samples.append(
        [time.monotonic() - t0, int(n), 1 if n else 0, 0]
      )
      _flush_occ()
      return orig_drain(*args, **kwargs)

    orch._ingest_coordinator_fill_tick = (  # type: ignore[method-assign]
      _fill_tick_with_occ
    )
    orch._drain_ingest_ready = _drain_with_occ  # type: ignore[method-assign]

  rc = 0
  try:
    database_startup()
    run_sync_timedb_supervisor_from_parsed(False, None, None)
  except BaseException:
    rc = 1
  if occ_path:
    Path(occ_path).write_text(json.dumps(occupancy_samples), encoding="utf-8")
  try:
    from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
      shutdown_codon_parse_pool,
    )

    shutdown_codon_parse_pool()
  except Exception:
    pass
  if done_path:
    Path(done_path).write_text(f"rc={rc}\n", encoding="utf-8")
  if _stack_fh is not None:
    _stack_fh.flush()
  # Hard exit — skip FT interpreter teardown with Codon/.so mapped.
  os._exit(rc)


if __name__ == "__main__":
  main()
