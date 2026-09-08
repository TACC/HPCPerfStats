from __future__ import annotations

from pathlib import Path

import pytest


def test_redis_url_uses_unix_socket_detects_unix_schemes():
  from hpcperfstats.dbload.lib import rediswait

  assert rediswait.redis_url_uses_unix_socket(
      "unix:///run/redis/redis.sock?db=1"
  )
  assert rediswait.redis_url_uses_unix_socket(
      "redis+unix:///run/redis/redis.sock?db=1"
  )
  assert not rediswait.redis_url_uses_unix_socket("redis://redis:6379/1")
  assert not rediswait.redis_url_uses_unix_socket("redis://127.0.0.1:6379/1")


def test_wait_for_redis_available_unix_skips_dns_wait(monkeypatch):
  import redis

  from hpcperfstats.dbload.lib import rediswait

  def fail_dns(*args, **kwargs):
    raise AssertionError("unix Redis URLs must not wait on TCP DNS")

  monkeypatch.setattr(rediswait, "wait_for_host_port_resolution", fail_dns)

  class FakeClient:
    def ping(self):
      return True

  monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: FakeClient())
  rediswait.wait_for_redis_available(
      "unix:///run/redis/redis.sock?db=1",
      timeout_seconds=1,
      interval_seconds=0.01,
      ping_timeout_seconds=0.01,
  )


def test_wait_for_redis_available_retries_until_ping_success(monkeypatch):
  import redis

  from hpcperfstats.dbload.lib import rediswait

  monkeypatch.setattr(
    rediswait,
    "wait_for_host_port_resolution",
    lambda *args, **kwargs: None,
  )

  attempt = {"n": 0}

  class FakeClient:
    def ping(self):
      attempt["n"] += 1
      if attempt["n"] < 3:
        raise redis.exceptions.ConnectionError("not ready")
      return True

  def fake_from_url(*args, **kwargs):
    return FakeClient()

  monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

  rediswait.wait_for_redis_available(
    "redis://fake:6379/1",
    timeout_seconds=1,
    interval_seconds=0.01,
    ping_timeout_seconds=0.01,
  )
  assert attempt["n"] == 3


def test_wait_for_redis_available_raises_timeout(monkeypatch):
  import redis

  from hpcperfstats.dbload.lib import rediswait

  monkeypatch.setattr(
    rediswait,
    "wait_for_host_port_resolution",
    lambda *args, **kwargs: None,
  )

  # Make time deterministic so the test can't be flaky.
  t = {"now": 0.0}

  def fake_time():
    return t["now"]

  def fake_sleep(interval):
    t["now"] += interval

  monkeypatch.setattr(rediswait.time, "time", fake_time)
  monkeypatch.setattr(rediswait.time, "sleep", fake_sleep)

  attempt = {"n": 0}

  class FakeClient:
    def ping(self):
      attempt["n"] += 1
      raise redis.exceptions.ConnectionError("unreachable")

  def fake_from_url(*args, **kwargs):
    return FakeClient()

  monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

  with pytest.raises(TimeoutError):
    rediswait.wait_for_redis_available(
      "redis://fake:6379/1",
      timeout_seconds=0.1,
      interval_seconds=0.05,
      ping_timeout_seconds=0.01,
    )

  assert attempt["n"] == 2


def test_django_startup_script_waits_for_redis():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  assert "wait_for_redis_available" in content
  assert "redis_wait_url" in content
  assert "redis://redis:6379/1" not in content


def test_supervisor_startup_script_waits_for_redis_without_tcp_hostname_fallback():
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "services-conf" / "supervisor_startup.sh").read_text()

  assert "wait_for_redis_available" in content
  assert "redis_wait_url" in content
  assert "redis://redis:6379/1" not in content


def test_redis_wait_url_prefers_ini_then_compose_unix(monkeypatch):
  from hpcperfstats.dbload.lib import rediswait

  monkeypatch.setattr(
      rediswait,
      "_ini_redis_location",
      lambda: "redis://example:6379/2",
  )
  assert rediswait.redis_wait_url() == "redis://example:6379/2"

  monkeypatch.setattr(rediswait, "_ini_redis_location", lambda: "")
  assert rediswait.redis_wait_url() == rediswait.COMPOSE_REDIS_UNIX_URL

  def boom() -> str:
    raise RuntimeError("ini unreadable")

  monkeypatch.setattr(rediswait, "_ini_redis_location", boom)
  assert rediswait.redis_wait_url() == rediswait.COMPOSE_REDIS_UNIX_URL


def test_redis_wait_url_remaps_compose_hostname_redis_to_unix(monkeypatch):
  """Baked redis://redis:6379/1 must not DNS-wait before redis joins the net."""
  from hpcperfstats.dbload.lib import rediswait

  assert rediswait.compose_redis_tcp_wait_should_use_unix(
      "redis://redis:6379/1"
  )
  assert not rediswait.compose_redis_tcp_wait_should_use_unix(
      "redis://example:6379/1"
  )
  monkeypatch.setattr(
      rediswait,
      "_ini_redis_location",
      lambda: "redis://redis:6379/1",
  )
  assert rediswait.redis_wait_url() == rediswait.COMPOSE_REDIS_UNIX_URL


def test_django_startup_invokes_spa_static_root_heal():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  assert "collectstatic --noinput" in content
  assert "ensure_spa_shells_from_django_settings" in content
  assert "hpcperfstats.site.lib.spa_static_root_heal" in content


def test_django_startup_compresses_static_sidecars_after_heal():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  collect_idx = content.index("collectstatic --noinput")
  heal_idx = content.index("ensure_spa_shells_from_django_settings")
  compress_idx = content.index(
      "-m hpcperfstats.site.lib.compress_static_sidecars"
  )
  gunicorn_idx = content.index("gunicorn")
  assert collect_idx < heal_idx < compress_idx < gunicorn_idx
  assert "manage.py makemigrations" not in content


@pytest.mark.machine_unit_mock
def test_django_startup_does_not_run_makemigrations():
  """Production startup must apply reviewed migrations only — never autogenerate."""
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  assert "manage.py migrate" in content
  # Comments may mention the forbidden command; the manage.py invocation must not.
  assert "manage.py makemigrations" not in content

