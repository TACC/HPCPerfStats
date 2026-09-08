"""Host unit tests for STATIC_ROOT Brotli/Gzip sidecar generation."""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import brotli
import pytest

from hpcperfstats.site.lib.compress_static_sidecars import (
  SIDECAR_MIN_BYTES,
  compress_static_sidecars,
  main,
  should_write_static_sidecars,
)


def _payload(size: int = SIDECAR_MIN_BYTES) -> bytes:
  return (b"static-payload-" * ((size // 15) + 1))[:size]


def _write(path: Path, data: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(data)


def test_should_write_skips_spa_html_and_tiny_files():
  big = SIDECAR_MIN_BYTES + 64
  assert should_write_static_sidecars("rest_framework/js/api.js", big) is True
  assert should_write_static_sidecars(
      "frontend/_next/static/chunks/app-abc123.js",
      big,
  ) is True
  assert should_write_static_sidecars(
      "frontend/machine/index.html",
      big,
  ) is False
  assert should_write_static_sidecars("frontend/pub/index.html", big) is False
  assert should_write_static_sidecars("machine/index.html", big) is False
  assert should_write_static_sidecars("pub/nested/shell.html", big) is False
  assert should_write_static_sidecars("admin/css/base.css", 16) is False
  assert should_write_static_sidecars("chunk.js.map", big) is False
  assert should_write_static_sidecars("font.woff2", big) is False


def test_compress_writes_br_gz_and_round_trips(tmp_path: Path):
  static_root = tmp_path / "staticfiles"
  js_path = static_root / "frontend" / "_next" / "static" / "chunks" / (
      "app-abc123.js"
  )
  css_path = static_root / "admin" / "css" / "base.css"
  spa_html = static_root / "frontend" / "machine" / "index.html"
  tiny = static_root / "rest_framework" / "js" / "tiny.js"
  raw = _payload(512)
  _write(js_path, raw)
  _write(css_path, raw)
  _write(spa_html, _payload(4096))
  _write(tiny, b"x" * 32)

  written, skipped = compress_static_sidecars(static_root)
  assert written == 2
  assert skipped >= 2

  for source in (js_path, css_path):
    br_path = Path(f"{source}.br")
    gz_path = Path(f"{source}.gz")
    assert br_path.is_file()
    assert gz_path.is_file()
    assert brotli.decompress(br_path.read_bytes()) == raw
    assert gzip.decompress(gz_path.read_bytes()) == raw
    assert source.is_file()

  assert not Path(f"{spa_html}.br").exists()
  assert not Path(f"{spa_html}.gz").exists()
  assert not Path(f"{tiny}.br").exists()


def test_compress_skips_fresh_sidecars_then_rewrites_on_newer_source(
    tmp_path: Path,
):
  static_root = tmp_path / "staticfiles"
  js_path = static_root / "frontend" / "_next" / "app.js"
  _write(js_path, _payload(300))
  written, _skipped = compress_static_sidecars(static_root)
  assert written == 1
  br_path = Path(f"{js_path}.br")
  first_mtime = br_path.stat().st_mtime

  written_again, skipped_again = compress_static_sidecars(static_root)
  assert written_again == 0
  assert skipped_again >= 1
  assert br_path.stat().st_mtime == first_mtime

  past = first_mtime - 120
  os.utime(br_path, (past, past))
  os.utime(Path(f"{js_path}.gz"), (past, past))
  os.utime(js_path, None)
  rewritten, _ = compress_static_sidecars(static_root)
  assert rewritten == 1
  assert br_path.stat().st_mtime > past


def test_compress_fails_closed_without_brotli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
  def _boom() -> object:
    raise RuntimeError(
        "Python brotli package is required to write static sidecars"
    )

  monkeypatch.setattr(
      "hpcperfstats.site.lib.compress_static_sidecars._load_brotli",
      _boom,
  )
  with pytest.raises(RuntimeError, match="brotli"):
    compress_static_sidecars(tmp_path / "missing-root")


def test_main_uses_argv_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
  js_path = tmp_path / "chunk.js"
  _write(js_path, _payload(300))
  assert main([str(tmp_path)]) == 0
  assert Path(f"{js_path}.br").is_file()
  out = capsys.readouterr().out
  assert "written=" in out
  assert str(tmp_path) in out


def test_pyproject_pins_brotli():
  pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
  text = pyproject.read_text(encoding="utf-8")
  assert '"brotli==1.2.0"' in text
