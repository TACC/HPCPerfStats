"""Unit tests for STATIC_ROOT SPA shell auto-heal (Vite volume → Next package)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hpcperfstats.site.lib.spa_static_root_heal import (
  REQUIRED_SPA_SHELLS,
  ensure_spa_shells_in_static_root,
  missing_required_shells,
  package_has_required_shells,
  resolve_package_frontend_dir,
)


def _write(path: Path, text: str = "shell") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")


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


def test_ensure_spa_shells_noop_when_already_present(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  package = tmp_path / "pkg" / "frontend"
  _write(package / "machine" / "index.html", "pkg-machine")
  _write(package / "pub" / "index.html", "pkg-pub")

  static_root = tmp_path / "staticfiles"
  volume = static_root / "frontend"
  _write(volume / "machine" / "index.html", "existing-machine")
  _write(volume / "pub" / "index.html", "existing-pub")

  ensure_spa_shells_in_static_root(
    static_root=static_root,
    package_frontend=package,
  )

  assert (volume / "machine" / "index.html").read_text(encoding="utf-8") == "existing-machine"
  out = capsys.readouterr().out
  assert "Verified SPA shells" in out
  assert "auto-healed" not in out


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
