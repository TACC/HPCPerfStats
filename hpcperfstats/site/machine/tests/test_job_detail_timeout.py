import threading
import time
from concurrent.futures import Future

import pytest


def _set_future_result_later(future, value, delay_seconds):
  def _run():
    time.sleep(delay_seconds)
    future.set_result(value)

  threading.Thread(target=_run, daemon=True).start()


@pytest.mark.django_db(databases=[])
def test_collect_future_results_with_deadline_returns_partial_results():
  from hpcperfstats.site.machine.api import _collect_future_results_with_deadline

  fast_future = Future()
  fast_future.set_result("fast")

  slow_future = Future()
  _set_future_result_later(slow_future, "slow", 0.2)

  future_to_key = {fast_future: "gpu", slow_future: "xalt"}

  start = time.time()
  results_by_key, remaining_keys = _collect_future_results_with_deadline(
    future_to_key, max_wait_seconds=0.05
  )
  elapsed = time.time() - start

  assert results_by_key == {"gpu": "fast"}
  assert remaining_keys == {"xalt"}
  # Ensure we didn't wait for the slow future.
  assert elapsed < 0.15


@pytest.mark.django_db(databases=[])
def test_collect_future_results_with_deadline_omits_failed_tasks():
  from hpcperfstats.site.machine.api import _collect_future_results_with_deadline

  ok_future = Future()
  ok_future.set_result("ok")

  bad_future = Future()
  bad_future.set_exception(RuntimeError("boom"))

  future_to_key = {ok_future: "fsio", bad_future: "schema"}

  results_by_key, remaining_keys = _collect_future_results_with_deadline(
    future_to_key, max_wait_seconds=0.5
  )

  assert results_by_key == {"fsio": "ok"}
  assert remaining_keys == set()

