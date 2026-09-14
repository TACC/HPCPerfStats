"""Host unit tests for atomic STATIC_ROOT / MEDIA_ROOT ram publish."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hpcperfstats.site.lib.staticfiles_ram_publish import (
    REQUIRED_STATIC_RELPATHS,
    main,
    publish_tree_to_ram,
)


def _write(path: Path, data: bytes | str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  if isinstance(data, str):
    path.write_text(data, encoding="utf-8")
  else:
    path.write_bytes(data)


def _static_src(tmp_path: Path) -> Path:
  src = tmp_path / "staticfiles"
  _write(src / "frontend" / "machine" / "index.html", "machine-shell")
  _write(src / "frontend" / "pub" / "index.html", "pub-shell")
  js = src / "frontend" / "_next" / "static" / "chunks" / "app.js"
  _write(js, b"console.log('app');\n" + b"x" * 64)
  _write(Path(f"{js}.br"), b"fake-br")
  return src


def test_publish_mirrors_frontend_and_sidecars(tmp_path: Path):
  src = _static_src(tmp_path)
  dest = tmp_path / "staticfiles-ram"
  publish_tree_to_ram(src, dest, required_relpaths=REQUIRED_STATIC_RELPATHS)
  machine = dest / "frontend" / "machine" / "index.html"
  pub = dest / "frontend" / "pub" / "index.html"
  js = dest / "frontend" / "_next" / "static" / "chunks" / "app.js"
  assert machine.read_text(encoding="utf-8") == "machine-shell"
  assert pub.read_text(encoding="utf-8") == "pub-shell"
  src_js = src / "frontend" / "_next" / "static" / "chunks" / "app.js"
  assert js.read_bytes() == src_js.read_bytes()
  assert Path(f"{js}.br").read_bytes() == b"fake-br"
  assert hashlib.sha256(machine.read_bytes()).hexdigest() == (
      hashlib.sha256((src / "frontend" / "machine" / "index.html").read_bytes())
      .hexdigest()
  )


def test_publish_is_atomic_on_preexisting_dest(tmp_path: Path):
  src = _static_src(tmp_path)
  dest = tmp_path / "staticfiles-ram"
  _write(dest / "frontend" / "stale-vite.js", "leftover")
  _write(dest / "frontend" / "machine" / "index.html", "old-shell")
  publish_tree_to_ram(src, dest, required_relpaths=REQUIRED_STATIC_RELPATHS)
  assert not (dest / "frontend" / "stale-vite.js").exists()
  assert (dest / "frontend" / "machine" / "index.html").read_text(
      encoding="utf-8"
  ) == "machine-shell"
  leftovers = [
      p.name
      for p in dest.iterdir()
      if p.name.startswith((".publish-", ".bak-"))
  ]
  assert leftovers == []


def test_publish_fail_closed_missing_shells(tmp_path: Path):
  src = tmp_path / "staticfiles"
  _write(src / "frontend" / "machine" / "index.html", "machine-only")
  dest = tmp_path / "staticfiles-ram"
  _write(dest / "frontend" / "machine" / "index.html", "keep-me")
  _write(dest / "frontend" / "pub" / "index.html", "keep-pub")
  with pytest.raises(SystemExit) as exc:
    publish_tree_to_ram(src, dest, required_relpaths=REQUIRED_STATIC_RELPATHS)
  assert exc.value.code == 1
  assert (dest / "frontend" / "machine" / "index.html").read_text(
      encoding="utf-8"
  ) == "keep-me"
  assert (dest / "frontend" / "pub" / "index.html").read_text(
      encoding="utf-8"
  ) == "keep-pub"


def test_publish_fail_closed_enospc_or_unwritable(tmp_path: Path):
  src = _static_src(tmp_path)
  dest = tmp_path / "staticfiles-ram"
  dest.mkdir()
  dest.chmod(0o555)
  try:
    with pytest.raises(SystemExit) as exc:
      publish_tree_to_ram(
          src, dest, required_relpaths=REQUIRED_STATIC_RELPATHS
      )
    assert exc.value.code == 1
  finally:
    dest.chmod(0o755)


def test_publish_src_equals_dest_fails(tmp_path: Path):
  src = _static_src(tmp_path)
  with pytest.raises(SystemExit) as exc:
    publish_tree_to_ram(
        src, src, required_relpaths=REQUIRED_STATIC_RELPATHS
    )
  assert exc.value.code == 1


def test_publish_media_empty_src_succeeds(tmp_path: Path):
  src = tmp_path / "media"
  dest = tmp_path / "media-ram"
  src.mkdir()
  publish_tree_to_ram(src, dest)
  assert dest.is_dir()
  assert list(dest.iterdir()) == []


def test_publish_media_mirrors_file(tmp_path: Path):
  src = tmp_path / "media"
  dest = tmp_path / "media-ram"
  payload = b"user-object-bytes"
  _write(src / "uploads" / "note.txt", payload)
  publish_tree_to_ram(src, dest)
  dest_file = dest / "uploads" / "note.txt"
  assert dest_file.read_bytes() == payload
  assert hashlib.sha256(dest_file.read_bytes()).hexdigest() == (
      hashlib.sha256(payload).hexdigest()
  )


def test_main_kind_static_and_media_use_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  static_src = _static_src(tmp_path)
  static_dest = tmp_path / "static-ram"
  media_src = tmp_path / "media"
  media_dest = tmp_path / "media-ram"
  media_src.mkdir()
  monkeypatch.setenv("STATIC_ROOT", str(static_src))
  monkeypatch.setenv("STATICFILES_RAM_ROOT", str(static_dest))
  monkeypatch.setenv("MEDIA_ROOT", str(media_src))
  monkeypatch.setenv("MEDIAFILES_RAM_ROOT", str(media_dest))
  assert main(["--kind", "static"]) == 0
  assert (static_dest / "frontend" / "machine" / "index.html").is_file()
  assert main(["--kind", "media"]) == 0
  assert media_dest.is_dir()
