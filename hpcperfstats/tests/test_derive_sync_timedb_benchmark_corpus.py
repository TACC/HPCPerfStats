"""Tests for scripts/derive_sync_timedb_benchmark_corpus.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "derive_sync_timedb_benchmark_corpus.py"


def _load_module():
  name = "derive_sync_timedb_benchmark_corpus"
  spec = importlib.util.spec_from_file_location(name, _SCRIPT)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules[name] = mod
  spec.loader.exec_module(mod)
  return mod


@pytest.fixture
def mod():
  return _load_module()


def _fixture_text(host: str = "orig.example.com") -> str:
  return (
      "$hpcperfstats 1.0\n"
      "$hostname %s\n"
      "!cpu user sys\n"
      "1000.0 job1 %s\n"
      "cpu 0 1 2\n"
      "1010.5 job1 %s\n"
      "cpu 0 3 4\n"
  ) % (host, host, host)


def test_rewrite_stats_identity_host_and_epochs(mod):
  text = _fixture_text("cn001.example.com")
  out = mod.rewrite_stats_identity(
      text,
      new_host="benchhost0001",
      epoch_offset=100,
  )
  assert "$hostname benchhost0001" in out
  assert "1100.0 job1 benchhost0001" in out
  assert "1110.5 job1 benchhost0001" in out
  assert "cn001.example.com" not in out
  assert "!cpu user sys" in out
  assert "cpu 0 1 2" in out


def test_derive_corpus_writes_outputs_and_manifest(mod, tmp_path):
  source_dir = tmp_path / "sources"
  host_dir = source_dir / "orig.example.com"
  host_dir.mkdir(parents=True)
  source_path = host_dir / "1000"
  source_path.write_text(_fixture_text(), encoding="utf-8")
  output_dir = tmp_path / "derived"

  manifest = mod.derive_corpus([source_path], output_dir)

  assert manifest["version"] == mod.MANIFEST_VERSION
  assert manifest["host_prefix"] == "benchhost"
  assert manifest["epoch_base_offset"] == 1_700_000_000
  assert len(manifest["entries"]) == 1
  entry = manifest["entries"][0]
  assert entry["original_host"] == "orig.example.com"
  assert entry["derived_host"] == "benchhost0000"
  assert entry["source_sha256_before"] == entry["source_sha256_after"]
  assert Path(entry["output_path"]).is_file()
  assert (output_dir / "manifest.json").is_file()
  manifest_disk = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
  assert manifest_disk["entries"][0]["output_sha256"] == entry["output_sha256"]


def test_derive_corpus_rejects_host_epoch_collision(mod):
  with pytest.raises(ValueError, match="overlap"):
    mod._assert_no_host_epoch_overlap(
        [
            ("benchhost0000", 1.0, 5.0),
            ("benchhost0000", 4.0, 8.0),
        ],
    )


def test_verify_source_unchanged_raises_on_hash_drift(mod, tmp_path):
  source_path = tmp_path / "stats"
  source_path.write_text(_fixture_text(), encoding="utf-8")
  manifest = {
      "entries": [
          {
              "source_path": str(source_path),
              "source_sha256_before": "deadbeef",
              "source_sha256_after": "cafebabe",
          },
      ],
  }
  with pytest.raises(ValueError, match="hash drift"):
    mod.verify_source_unchanged(manifest)


def test_verify_source_unchanged_passes_for_stable_manifest(mod, tmp_path):
  source_path = tmp_path / "host" / "1000"
  source_path.parent.mkdir(parents=True)
  source_path.write_text(_fixture_text(), encoding="utf-8")
  digest = mod._sha256_file(source_path)
  manifest = {
      "entries": [
          {
              "source_path": str(source_path),
              "source_sha256_before": digest,
              "source_sha256_after": digest,
          },
      ],
  }
  mod.verify_source_unchanged(manifest)


def test_manifest_entry_keys(mod, tmp_path):
  source_path = tmp_path / "src"
  source_path.write_text(_fixture_text(), encoding="utf-8")
  manifest = mod.derive_corpus([source_path], tmp_path / "out", dry_run=True)
  entry = manifest["entries"][0]
  expected = {
      "source_path",
      "source_sha256_before",
      "source_sha256_after",
      "output_path",
      "output_sha256",
      "original_host",
      "derived_host",
      "epoch_offset",
      "epoch_min",
      "epoch_max",
      "shifted_first_epoch",
  }
  assert expected.issubset(entry.keys())


def test_derive_corpus_dry_run_writes_no_outputs(mod, tmp_path):
  source_path = tmp_path / "src"
  source_path.write_text(_fixture_text(), encoding="utf-8")
  output_dir = tmp_path / "out"
  manifest = mod.derive_corpus([source_path], output_dir, dry_run=True)
  assert manifest["dry_run"] is True
  assert not (output_dir / "manifest.json").exists()
  assert not any(output_dir.iterdir()) if output_dir.exists() else True
