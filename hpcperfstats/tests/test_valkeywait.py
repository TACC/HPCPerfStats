from __future__ import annotations

from pathlib import Path

import pytest


def test_valkey_url_uses_unix_socket_detects_unix_scheme():
  from hpcperfstats.dbload.lib import valkeywait

  assert valkeywait.valkey_url_uses_unix_socket(
    "unix:///run/valkey/valkey.sock?db=1"
  )
  assert not valkeywait.valkey_url_uses_unix_socket("valkey://valkey:6379/1")
  assert not valkeywait.valkey_url_uses_unix_socket("valkey://127.0.0.1:6379/1")


def test_wait_for_valkey_available_unix_skips_dns_wait(monkeypatch):
  import valkey

  from hpcperfstats.dbload.lib import valkeywait

  def fail_dns(*args, **kwargs):
    raise AssertionError("unix Valkey URLs must not wait on TCP DNS")

  monkeypatch.setattr(valkeywait, "wait_for_host_port_resolution", fail_dns)

  class FakeClient:
    def ping(self):
      return True

  monkeypatch.setattr(valkey.Valkey, "from_url", lambda *a, **k: FakeClient())
  valkeywait.wait_for_valkey_available(
    "unix:///run/valkey/valkey.sock?db=1",
    timeout_seconds=1,
    interval_seconds=0.01,
    ping_timeout_seconds=0.01,
  )


def test_wait_for_valkey_available_retries_until_ping_success(monkeypatch):
  import valkey

  from hpcperfstats.dbload.lib import valkeywait

  monkeypatch.setattr(
    valkeywait,
    "wait_for_host_port_resolution",
    lambda *args, **kwargs: None,
  )

  attempt = {"n": 0}

  class FakeClient:
    def ping(self):
      attempt["n"] += 1
      if attempt["n"] < 3:
        raise valkey.exceptions.ConnectionError("not ready")
      return True

  monkeypatch.setattr(valkey.Valkey, "from_url", lambda *a, **k: FakeClient())

  valkeywait.wait_for_valkey_available(
    "valkey://fake:6379/1",
    timeout_seconds=1,
    interval_seconds=0.01,
    ping_timeout_seconds=0.01,
  )
  assert attempt["n"] == 3


def test_wait_for_valkey_available_raises_timeout(monkeypatch):
  import valkey

  from hpcperfstats.dbload.lib import valkeywait

  monkeypatch.setattr(
    valkeywait,
    "wait_for_host_port_resolution",
    lambda *args, **kwargs: None,
  )

  # Make time deterministic so the test can't be flaky.
  t = {"now": 0.0}

  def fake_time():
    return t["now"]

  def fake_sleep(interval):
    t["now"] += interval

  monkeypatch.setattr(valkeywait.time, "time", fake_time)
  monkeypatch.setattr(valkeywait.time, "sleep", fake_sleep)

  attempt = {"n": 0}

  class FakeClient:
    def ping(self):
      attempt["n"] += 1
      raise valkey.exceptions.ConnectionError("unreachable")

  monkeypatch.setattr(valkey.Valkey, "from_url", lambda *a, **k: FakeClient())

  with pytest.raises(TimeoutError):
    valkeywait.wait_for_valkey_available(
      "valkey://fake:6379/1",
      timeout_seconds=0.1,
      interval_seconds=0.05,
      ping_timeout_seconds=0.01,
    )

  assert attempt["n"] == 2


@pytest.mark.parametrize(
  "script", ["django_startup.sh", "supervisor_startup.sh"]
)
def test_startup_scripts_wait_for_valkey_without_tcp_hostname(script):
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "services-conf" / script).read_text()

  assert "wait_for_valkey_available" in content
  assert "valkey_wait_url" in content
  assert "valkey://valkey:6379/1" not in content


def test_valkey_wait_url_prefers_ini_then_compose_unix(monkeypatch):
  from hpcperfstats.dbload.lib import conf_parser as cfg, valkeywait

  monkeypatch.setattr(
    cfg, "get_valkey_location", lambda: "valkey://example:6379/2"
  )
  assert valkeywait.valkey_wait_url() == "valkey://example:6379/2"

  def boom() -> str:
    raise RuntimeError("ini unreadable")

  monkeypatch.setattr(cfg, "get_valkey_location", boom)
  assert valkeywait.valkey_wait_url() == valkeywait.COMPOSE_VALKEY_UNIX_URL


def test_valkey_wait_url_remaps_compose_hostname_valkey_to_unix(monkeypatch):
  """A valkey://valkey:6379/1 INI must not DNS-wait before valkey joins."""
  from hpcperfstats.dbload.lib import conf_parser as cfg, valkeywait

  assert valkeywait.compose_valkey_tcp_wait_should_use_unix(
    "valkey://valkey:6379/1"
  )
  assert not valkeywait.compose_valkey_tcp_wait_should_use_unix(
    "valkey://example:6379/1"
  )
  monkeypatch.setattr(
    cfg, "get_valkey_location", lambda: "valkey://valkey:6379/1"
  )
  assert valkeywait.valkey_wait_url() == valkeywait.COMPOSE_VALKEY_UNIX_URL


def test_django_startup_invokes_spa_static_root_heal():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  assert "collectstatic --noinput --clear" in content
  assert "ensure_spa_shells_from_django_settings" in content
  assert "hpcperfstats.site.lib.spa_static_root_heal" in content


def test_django_startup_compresses_static_sidecars_after_heal():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  collect_idx = content.index("collectstatic --noinput --clear")
  heal_idx = content.index("ensure_spa_shells_from_django_settings")
  compress_idx = content.index(
    "-m hpcperfstats.site.lib.compress_static_sidecars"
  )
  gunicorn_idx = content.index("gunicorn")
  assert collect_idx < heal_idx < compress_idx < gunicorn_idx
  assert "manage.py makemigrations" not in content


def test_django_startup_publishes_ram_after_compress():
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  collect_idx = content.index("collectstatic --noinput --clear")
  heal_idx = content.index("ensure_spa_shells_from_django_settings")
  compress_idx = content.index(
    "-m hpcperfstats.site.lib.compress_static_sidecars"
  )
  static_idx = content.index(
    "-m hpcperfstats.site.lib.staticfiles_ram_publish --kind static"
  )
  media_idx = content.index(
    "-m hpcperfstats.site.lib.staticfiles_ram_publish --kind media"
  )
  gunicorn_idx = content.index("/usr/local/bin/gunicorn")
  assert (
    collect_idx
    < heal_idx
    < compress_idx
    < static_idx
    < media_idx
    < gunicorn_idx
  )
  assert "manage.py makemigrations" not in content


@pytest.mark.machine_unit_mock
def test_django_startup_does_not_run_makemigrations():
  """Production startup must apply reviewed migrations only - never autogenerate."""
  repo_root = Path(__file__).resolve().parents[2]
  script_path = repo_root / "services-conf" / "django_startup.sh"
  content = script_path.read_text()

  assert "manage.py migrate" in content
  # Comments may mention the forbidden command; the manage.py invocation must not.
  assert "manage.py makemigrations" not in content
