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


def test_flat_mount_root_materializes_pems(resolve_mod, tmp_path: Path):
  mount = tmp_path / "mount"
  mount.mkdir()
  (mount / "fullchain.pem").write_text("CHAIN", encoding="utf-8")
  (mount / "privkey.pem").write_text("KEY", encoding="utf-8")
  dest = tmp_path / "dest"
  resolve_mod.materialize_ssl_certs(mount, dest)
  assert (dest / "fullchain.pem").is_file() and not (dest / "fullchain.pem").is_symlink()
  assert (dest / "privkey.pem").is_file() and not (dest / "privkey.pem").is_symlink()
  assert (dest / "fullchain.pem").read_text(encoding="utf-8") == "CHAIN"


def test_letsencrypt_rel_resolves_archive_symlinks(resolve_mod, tmp_path: Path):
  """LE live/*.pem are relative links into archive/; materialize real files."""
  mount = tmp_path / "letsencrypt"
  archive = mount / "archive" / "host.example"
  live = mount / "live" / "host.example"
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

  dest = tmp_path / "dest"
  resolve_mod.materialize_ssl_certs(
      mount,
      dest,
      ssl_certs_rel="live/host.example",
  )
  fullchain = dest / "fullchain.pem"
  privkey = dest / "privkey.pem"
  assert fullchain.is_file() and not fullchain.is_symlink()
  assert privkey.is_file() and not privkey.is_symlink()
  assert fullchain.read_text(encoding="utf-8") == "CHAIN"
  assert privkey.read_text(encoding="utf-8") == "KEY"
  assert stat.S_IMODE(fullchain.stat().st_mode) == 0o644
  assert stat.S_IMODE(privkey.stat().st_mode) == 0o600


def test_symlink_escape_outside_mount_fails_closed(resolve_mod, tmp_path: Path):
  mount = tmp_path / "mount"
  outside = tmp_path / "outside"
  live = mount / "live" / "host"
  live.mkdir(parents=True)
  outside.mkdir()
  (outside / "fullchain.pem").write_text("CHAIN", encoding="utf-8")
  (outside / "privkey.pem").write_text("KEY", encoding="utf-8")
  (live / "fullchain.pem").symlink_to(outside / "fullchain.pem")
  (live / "privkey.pem").symlink_to(outside / "privkey.pem")

  with pytest.raises(ValueError, match="outside ssl source mount"):
    resolve_mod.materialize_ssl_certs(
        mount,
        tmp_path / "dest",
        ssl_certs_rel="live/host",
    )


def test_rel_path_escape_mount_fails_closed(resolve_mod, tmp_path: Path):
  mount = tmp_path / "mount"
  mount.mkdir()
  (mount / "fullchain.pem").write_text("c", encoding="utf-8")
  (mount / "privkey.pem").write_text("k", encoding="utf-8")
  with pytest.raises(ValueError, match="outside ssl source mount"):
    resolve_mod.resolve_source_certs_dir(mount, ssl_certs_rel="../outside")


def test_copy_pems_preserving_meta(resolve_mod, tmp_path: Path):
  src = tmp_path / "src"
  src.mkdir(mode=0o750)
  os.chmod(src, 0o750)
  fullchain = src / "fullchain.pem"
  privkey = src / "privkey.pem"
  fullchain.write_text("CHAIN", encoding="utf-8")
  privkey.write_text("KEY", encoding="utf-8")
  os.chmod(fullchain, 0o644)
  os.chmod(privkey, 0o600)

  dest = tmp_path / "out"
  resolve_mod.copy_pems_preserving_meta(src, dest)
  assert stat.S_IMODE(dest.stat().st_mode) == 0o750
  out_full = dest / "fullchain.pem"
  out_key = dest / "privkey.pem"
  assert out_full.is_file() and not out_full.is_symlink()
  assert out_key.is_file() and not out_key.is_symlink()
  assert stat.S_IMODE(out_full.stat().st_mode) == 0o644
  assert stat.S_IMODE(out_key.stat().st_mode) == 0o600


def test_main_fixture_materializes(resolve_mod, tmp_path: Path, capsys):
  dest = tmp_path / "out"
  assert resolve_mod.main(
      ["--fixture", "--dest-dir", str(dest)]
  ) == 0
  assert (dest / "fullchain.pem").is_file()
  err = capsys.readouterr().err.strip()
  assert err == str(dest.resolve())


def test_main_fails_closed_on_missing_mount(resolve_mod, tmp_path: Path, capsys):
  missing = tmp_path / "missing"
  assert resolve_mod.main(
      ["--ssl-source-mount", str(missing), "--dest-dir", str(tmp_path / "out")]
  ) == 1
  err = capsys.readouterr().err
  assert "error:" in err
