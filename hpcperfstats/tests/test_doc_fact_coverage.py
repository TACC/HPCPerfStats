"""scripts/doc_fact_coverage.py extracts doc facts and checks ledger coverage."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = (
  Path(__file__).resolve().parents[2] / "scripts" / "doc_fact_coverage.py"
)
_spec = importlib.util.spec_from_file_location("doc_fact_coverage", _SCRIPT)
dfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dfc)

SOURCE = """# Old page

Set `[PIPELINE] archive_zstd_threads` to 4 and pass --dry-run.
Export HPC_SITE_NAME before you start; the pin is RabbitMQ 4.4.2.
The seal step reads `hpcperfstats/listend.py` and waits 480s on 64 GiB hosts.
See https://example.org/a/b.md for background.

```ini
[PORTAL]
gunicorn_workers = 8
```

```bash
podman-compose -p hpcperfstats ps \\
  --format json
# comment
```
"""


def _facts(tmp_path: Path) -> dict[str, str]:
  src = tmp_path / "old.md"
  src.write_text(SOURCE, encoding="utf-8")
  facts: dict[str, str] = {}
  for token, kind, _n in dfc.extract_facts(src):
    facts.setdefault(token, kind)
  return facts


def _ledger(tmp_path: Path, rows: list[str]) -> Path:
  path = tmp_path / "ledger.md"
  path.write_text(dfc.HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
  return path


def test_extract_finds_keys_flags_env_versions_quantities_paths(tmp_path):
  facts = _facts(tmp_path)
  assert facts["[PIPELINE] archive_zstd_threads"] == "ini-key"
  assert facts["[PORTAL] gunicorn_workers"] == "ini-key"
  assert facts["--dry-run"] == "flag"
  assert facts["--format"] == "flag"
  assert facts["HPC_SITE_NAME"] == "env"
  assert facts["4.4.2"] == "version"
  assert facts["480s"] == "quantity"
  assert facts["64 GiB"] == "quantity"
  assert facts["hpcperfstats/listend.py"] == "path"
  assert facts["podman-compose -p hpcperfstats ps"] == "command"
  assert "--format json" not in facts
  assert "# comment" not in facts
  assert not any("example.org" in token for token in facts)


def test_extract_ledger_round_trips_through_parser(tmp_path):
  src = tmp_path / "old.md"
  src.write_text("Run `a|b` and `x`y``.\n", encoding="utf-8")
  ledger = dfc.render_ledger([src])
  tokens = [row[1] for row in dfc.parse_ledger(ledger)]
  assert "a|b" in tokens
  assert all(row[3] == "" for row in dfc.parse_ledger(ledger))


def test_check_fails_on_missing_keep_token(tmp_path):
  dest = tmp_path / "new.md"
  dest.write_text("Pass `--dry-run`.\n", encoding="utf-8")
  ledger = _ledger(
    tmp_path,
    [
      "| `--dry-run` | flag | old.md:3 | keep |  |",
      "| `HPC_SITE_NAME` | env | old.md:4 | keep |  |",
    ],
  )
  errors = dfc.check_ledger(ledger, [dest])
  assert len(errors) == 1
  assert "HPC_SITE_NAME" in errors[0] and "missing" in errors[0]


def test_check_passes_with_evidence_noted_drop(tmp_path):
  dest = tmp_path / "new.md"
  dest.write_text("Pass `--dry-run`.\n", encoding="utf-8")
  ledger = _ledger(
    tmp_path,
    [
      "| `--dry-run` | flag | old.md:3 | keep |  |",
      "| `--old` | flag | old.md:9 | drop | argparse in run.py has no --old |",
    ],
  )
  assert dfc.check_ledger(ledger, [dest]) == []
  res = subprocess.run(
    [sys.executable, str(_SCRIPT), "check", str(ledger), str(dest)],
    capture_output=True,
    text=True,
  )
  assert res.returncode == 0
  assert "DOC_FACT_COVERAGE_OK 2 rows" in res.stdout


def test_check_fails_drop_without_evidence_ask_and_blank(tmp_path):
  dest = tmp_path / "new.md"
  dest.write_text("nothing\n", encoding="utf-8")
  ledger = _ledger(
    tmp_path,
    [
      "| `--old` | flag | old.md:9 | drop |  |",
      "| `480s` | quantity | old.md:5 | ask | owner? |",
      "| `64 GiB` | quantity | old.md:5 |  |  |",
      "| `4.4.3` | version | old.md:4 | fix |  |",
    ],
  )
  errors = dfc.check_ledger(ledger, [dest])
  assert any("drop needs an Evidence" in e for e in errors)
  assert any("status ask" in e for e in errors)
  assert any("<blank>" in e for e in errors)
  assert any("fix needs an Evidence" in e for e in errors)
  assert any("4.4.3" in e and "missing" in e for e in errors)
  res = subprocess.run(
    [sys.executable, str(_SCRIPT), "check", str(ledger), str(dest)],
    capture_output=True,
    text=True,
  )
  assert res.returncode == 1
  assert "--old" in res.stdout
