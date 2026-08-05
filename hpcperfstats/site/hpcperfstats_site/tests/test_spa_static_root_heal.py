"""Unit tests for STATIC_ROOT SPA shell auto-heal (Vite volume → Next package)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hpcperfstats.site.lib.spa_static_root_heal import (
  REQUIRED_SPA_SHELLS,
  ensure_spa_shells_in_static_root,
  missing_required_shells,
  package_has_required_shells,
  purge_nginx_config_from_public_frontend,
  resolve_package_frontend_dir,
  spa_shell_fingerprint,
)


def _write(path: Path, text: str = "shell") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")


def test_spa_shell_fingerprint_hashes_machine_index(tmp_path: Path):
  frontend = tmp_path / "frontend"
  _write(frontend / "machine" / "index.html", "content-a")
  fp_a = spa_shell_fingerprint(frontend)
  assert len(fp_a) == 64
  _write(frontend / "machine" / "index.html", "content-b")
  assert spa_shell_fingerprint(frontend) != fp_a
  assert spa_shell_fingerprint(tmp_path / "missing") == ""


def test_ensure_spa_shells_heals_vite_volume(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
  package = tmp_path / "pkg" / "frontend"
  _write(package / "machine" / "index.html", "machine-pkg")
  _write(package / "pub" / "index.html", "pub-pkg")
  _write(package / "_next" / "chunk.js", "asset")

  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "index.html", "vite-root")
  (volume / ".vite").mkdir()
  _write(volume / "assets" / "index.js", "old")

  ensure_spa_shells_in_static_root(
    static_root=static_root,
    package_frontend=package,
  )

  assert (volume / "machine" / "index.html").read_text(encoding="utf-8") == "machine-pkg"
  assert (volume / "pub" / "index.html").read_text(encoding="utf-8") == "pub-pkg"
  assert not (volume / ".vite").exists()
  assert not (volume / "index.html").is_file()
  out = capsys.readouterr().out
  assert "auto-healed from package static" in out
  assert "Verified SPA shells in STATIC_ROOT" in out


def test_ensure_spa_shells_fails_when_package_missing(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  package = tmp_path / "pkg" / "frontend"
  package.mkdir(parents=True)
  _write(package / "index.html", "incomplete")

  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "index.html", "vite-root")
  (volume / ".vite").mkdir()

  with pytest.raises(SystemExit) as exc:
    ensure_spa_shells_in_static_root(
      static_root=static_root,
      package_frontend=package,
    )
  assert exc.value.code == 1
  err = capsys.readouterr().err
  assert "collectstatic did not produce required SPA shell(s)" in err
  assert "stale volume marker" in err
  assert "hpcperfstats-full" in err
  # Volume must not have been emptied/replaced with incomplete package.
  assert (volume / "index.html").is_file()
  assert missing_required_shells(volume)


def test_ensure_spa_shells_noop_when_fingerprints_match(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  """Shells present with identical machine/index.html content → noop."""
  package = tmp_path / "pkg" / "frontend"
  _write(package / "machine" / "index.html", "same-shell")
  _write(package / "pub" / "index.html", "pkg-pub")
  _write(package / "_next" / "new.js", "pkg-asset")

  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "machine" / "index.html", "same-shell")
  _write(volume / "pub" / "index.html", "vol-pub")
  _write(volume / "_next" / "old.js", "vol-asset")

  ensure_spa_shells_in_static_root(
    static_root=static_root,
    package_frontend=package,
  )

  assert (volume / "machine" / "index.html").read_text(encoding="utf-8") == "same-shell"
  assert (volume / "_next" / "old.js").is_file()
  assert not (volume / "_next" / "new.js").is_file()
  out = capsys.readouterr().out
  assert "Verified SPA shells" in out
  assert "auto-healed" not in out
  assert "fingerprint" not in out.lower()


def test_ensure_spa_shells_replaces_on_fingerprint_drift(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  """Shells present but machine/index.html content differs → replace volume."""
  package = tmp_path / "pkg" / "frontend"
  _write(package / "machine" / "index.html", "pkg-machine-new")
  _write(package / "pub" / "index.html", "pkg-pub")
  _write(package / "_next" / "chunk.js", "new-asset")

  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "machine" / "index.html", "vol-machine-old")
  _write(volume / "pub" / "index.html", "vol-pub")
  _write(volume / "_next" / "stale.js", "old-asset")

  ensure_spa_shells_in_static_root(
    static_root=static_root,
    package_frontend=package,
  )

  assert (volume / "machine" / "index.html").read_text(encoding="utf-8") == "pkg-machine-new"
  assert (volume / "pub" / "index.html").read_text(encoding="utf-8") == "pkg-pub"
  assert (volume / "_next" / "chunk.js").is_file()
  assert not (volume / "_next" / "stale.js").is_file()
  out = capsys.readouterr().out
  assert "fingerprint" in out.lower()
  assert "auto-healed from package static" in out
  assert "Verified SPA shells in STATIC_ROOT" in out


def test_package_has_required_shells_and_resolve(tmp_path: Path):
  static_dir = tmp_path / "static"
  frontend = static_dir / "frontend"
  for rel in REQUIRED_SPA_SHELLS:
    _write(frontend / rel)
  assert package_has_required_shells(frontend)
  resolved = resolve_package_frontend_dir(
    staticfiles_dirs=(static_dir,),
    settings_dir=tmp_path,
  )
  assert resolved == frontend


def test_purge_nginx_config_from_public_frontend_removes_inc(tmp_path: Path):
  frontend = tmp_path / "frontend"
  _write(frontend / "machine" / "index.html", "ok")
  _write(frontend / "nginx-csp-machine.inc", "add_header Content-Security-Policy \"x\";\n")
  _write(frontend / "notes.md", "# no")
  removed = purge_nginx_config_from_public_frontend(frontend)
  assert "nginx-csp-machine.inc" in removed
  assert "notes.md" in removed
  assert not (frontend / "nginx-csp-machine.inc").exists()
  assert (frontend / "machine" / "index.html").is_file()


def test_ensure_spa_shells_purges_leaked_nginx_inc(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  package = tmp_path / "pkg" / "frontend"
  _write(package / "machine" / "index.html", "same")
  _write(package / "pub" / "index.html", "same")
  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "machine" / "index.html", "same")
  _write(volume / "pub" / "index.html", "same")
  _write(volume / "nginx-csp-pub.inc", "leak")

  ensure_spa_shells_in_static_root(static_root=static_root, package_frontend=package)
  assert not (volume / "nginx-csp-pub.inc").exists()
  assert "Purged non-web leftovers" in capsys.readouterr().out
