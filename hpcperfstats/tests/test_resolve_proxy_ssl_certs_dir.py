"""Unit tests for services-conf/resolve_proxy_ssl_certs_dir.py."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HELPER_PATH = _REPO_ROOT / "services-conf" / "resolve_proxy_ssl_certs_dir.py"


def _load_helper():
  spec = importlib.util.spec_from_file_location(
      "resolve_proxy_ssl_certs_dir",
      _HELPER_PATH,
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


@pytest.fixture(scope="module")
def resolve_mod():
  return _load_helper()


def test_fixture_dir_has_required_pems(resolve_mod):
  path = resolve_mod.validate_ssl_certs_dir(resolve_mod.fixture_ssl_certs_dir())
  assert (path / "fullchain.pem").is_file()
  assert (path / "privkey.pem").is_file()


def test_validate_fails_without_fullchain(resolve_mod, tmp_path: Path):
  (tmp_path / "privkey.pem").write_text("k", encoding="utf-8")
  with pytest.raises(ValueError, match="fullchain.pem"):
    resolve_mod.validate_ssl_certs_dir(tmp_path)


def test_validate_fails_without_privkey(resolve_mod, tmp_path: Path):
  (tmp_path / "fullchain.pem").write_text("c", encoding="utf-8")
  with pytest.raises(ValueError, match="privkey.pem"):
    resolve_mod.validate_ssl_certs_dir(tmp_path)


def test_validate_fails_when_not_a_directory(resolve_mod, tmp_path: Path):
  missing = tmp_path / "nope"
  with pytest.raises(ValueError, match="not a directory"):
    resolve_mod.validate_ssl_certs_dir(missing)


def test_validate_reports_broken_symlink(resolve_mod, tmp_path: Path):
  (tmp_path / "fullchain.pem").symlink_to("missing-target.pem")
  (tmp_path / "privkey.pem").write_text("k", encoding="utf-8")
  with pytest.raises(ValueError, match="broken symlink"):
    resolve_mod.validate_ssl_certs_dir(tmp_path)


def test_load_from_ini_success(resolve_mod, tmp_path: Path):
  fix = resolve_mod.fixture_ssl_certs_dir()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(
      f"[DEFAULT]\nssl_certs_dir = {fix}\n",
      encoding="utf-8",
  )
  assert resolve_mod.load_ssl_certs_dir_from_ini(ini) == fix


def test_load_from_ini_missing_key(resolve_mod, tmp_path: Path):
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text("[DEFAULT]\nmachine = x\n", encoding="utf-8")
  with pytest.raises(ValueError, match="ssl_certs_dir"):
    resolve_mod.load_ssl_certs_dir_from_ini(ini)


def test_main_fixture_prints_path(resolve_mod, capsys):
  assert resolve_mod.main(["--fixture", "--no-link"]) == 0
  out = capsys.readouterr().out.strip()
  assert Path(out) == resolve_mod.fixture_ssl_certs_dir()


def test_main_fails_closed_on_bad_ini(resolve_mod, tmp_path: Path, capsys):
  ini = tmp_path / "bad.ini"
  ini.write_text("[DEFAULT]\nssl_certs_dir = /no/such/dir\n", encoding="utf-8")
  assert resolve_mod.main(["--ini", str(ini)]) == 1
  err = capsys.readouterr().err
  assert "error:" in err


def test_ensure_compose_copies_pems_preserving_mode(
    resolve_mod, tmp_path: Path
):
  src = tmp_path / "src"
  src.mkdir(mode=0o750)
  os.chmod(src, 0o750)
  fullchain = src / "fullchain.pem"
  privkey = src / "privkey.pem"
  fullchain.write_text("CHAIN", encoding="utf-8")
  privkey.write_text("KEY", encoding="utf-8")
  os.chmod(fullchain, 0o644)
  os.chmod(privkey, 0o600)

  dest = resolve_mod.ensure_compose_ssl_certs_context(src, checkout_root=tmp_path)
  assert dest.is_dir()
  assert not dest.is_symlink()
  assert stat.S_IMODE(dest.stat().st_mode) == 0o750
  out_full = dest / "fullchain.pem"
  out_key = dest / "privkey.pem"
  assert out_full.is_file() and not out_full.is_symlink()
  assert out_key.is_file() and not out_key.is_symlink()
  assert out_full.read_text(encoding="utf-8") == "CHAIN"
  assert out_key.read_text(encoding="utf-8") == "KEY"
  assert stat.S_IMODE(out_full.stat().st_mode) == 0o644
  assert stat.S_IMODE(out_key.stat().st_mode) == 0o600


def test_ensure_compose_copies_through_letsencrypt_live_relative_symlinks(
    resolve_mod, tmp_path: Path
):
  """LE live/*.pem are relative links into archive/; materialize real files."""
  archive = tmp_path / "archive" / "host.example"
  live = tmp_path / "live" / "host.example"
  archive.mkdir(parents=True)
  live.mkdir(parents=True)
  os.chmod(live, 0o755)
  full_src = archive / "fullchain1.pem"
  key_src = archive / "privkey1.pem"
  full_src.write_text("CHAIN", encoding="utf-8")
  key_src.write_text("KEY", encoding="utf-8")
  os.chmod(full_src, 0o644)
  os.chmod(key_src, 0o600)
  (live / "fullchain.pem").symlink_to("../../archive/host.example/fullchain1.pem")
  (live / "privkey.pem").symlink_to("../../archive/host.example/privkey1.pem")

  dest = resolve_mod.ensure_compose_ssl_certs_context(live, checkout_root=tmp_path)
  fullchain = dest / "fullchain.pem"
  privkey = dest / "privkey.pem"
  assert fullchain.is_file() and not fullchain.is_symlink()
  assert privkey.is_file() and not privkey.is_symlink()
  assert fullchain.read_text(encoding="utf-8") == "CHAIN"
  assert privkey.read_text(encoding="utf-8") == "KEY"
  assert stat.S_IMODE(dest.stat().st_mode) == 0o755
  assert stat.S_IMODE(fullchain.stat().st_mode) == 0o644
  assert stat.S_IMODE(privkey.stat().st_mode) == 0o600


def test_ensure_compose_ssl_certs_context_replaces_stale_dir_symlink(
    resolve_mod, tmp_path: Path
):
  fix = resolve_mod.fixture_ssl_certs_dir()
  dest = tmp_path / resolve_mod.COMPOSE_SSL_CERTS_CONTEXT_REL
  dest.symlink_to(tmp_path / "old-missing-target")
  resolve_mod.ensure_compose_ssl_certs_context(fix, checkout_root=tmp_path)
  assert dest.is_dir()
  assert (dest / "fullchain.pem").is_file()
  assert not (dest / "fullchain.pem").is_symlink()


def test_main_from_ini_updates_compose_context(
    resolve_mod, tmp_path: Path, capsys
):
  fix = resolve_mod.fixture_ssl_certs_dir()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(
      f"[DEFAULT]\nssl_certs_dir = {fix}\n",
      encoding="utf-8",
  )
  assert (
      resolve_mod.main(
          ["--ini", str(ini), "--repo-root", str(tmp_path)]
      )
      == 0
  )
  out = capsys.readouterr().out.strip()
  assert Path(out) == fix
  dest = tmp_path / resolve_mod.COMPOSE_SSL_CERTS_CONTEXT_REL
  assert dest.is_dir()
  assert (dest / "fullchain.pem").is_file()
  assert not (dest / "fullchain.pem").is_symlink()


def test_under_host_prefix_maps_absolute_path(resolve_mod):
  assert resolve_mod.under_host_prefix(Path("/etc/x"), Path("/host")) == Path(
      "/host/etc/x"
  )
  assert resolve_mod.under_host_prefix(Path("/etc/x"), None) == Path("/etc/x")


def test_main_dest_dir_bakes_pems(resolve_mod, tmp_path: Path, capsys):
  fix = resolve_mod.fixture_ssl_certs_dir()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(f"[DEFAULT]\nssl_certs_dir = {fix}\n", encoding="utf-8")
  dest = tmp_path / "image-ssl"
  assert (
      resolve_mod.main(
          [
              "--ini",
              str(ini),
              "--dest-dir",
              str(dest),
              "--host-prefix",
              "/",
          ]
      )
      == 0
  )
  # host-prefix / keeps paths as-is on a normal host
  assert (dest / "fullchain.pem").is_file()
  assert (dest / "privkey.pem").is_file()
  assert not (tmp_path / resolve_mod.COMPOSE_SSL_CERTS_CONTEXT_REL).exists()
