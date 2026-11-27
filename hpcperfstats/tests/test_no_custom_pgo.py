"""Regression: custom Clang profile-guided bake surface must stay removed."""

from __future__ import annotations

from pathlib import Path

# Split so this module itself does not contain the full forbidden literals.
_FORBIDDEN = (
  "PGO" + "_PHASE",
  "PGO" + "ROOT",
  "pgo_" + "lib.sh",
  "pgo_" + "clang_flags",
  "pgo_" + "namespaces",
  "pgo_" + "profiles",
  "--" + "full-pgo",
  "-f" + "profile-instr",
  "alpine_" + "pgo_profile",
)

_PRODUCT_GLOBS = (
  "services-conf/pipeline_web.Dockerfile",
  "docker-compose.defaults.yaml",
  "docker-compose.yaml.example",
  "docker-compose.yaml",
  "README.md",
  "docs/how-to/develop/*.md",
  "docs/reference/test-layout.md",
  "docs/upgrade.md",
  "services-conf/*.sh",
  "services-conf/*.Dockerfile",
  "services-conf/*.yaml",
  "scripts/*.sh",
  "scripts/lib/*.sh",
  "hpcperfstats/cursor-rules/*.mdc",
)


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def test_no_custom_pgo_surface_remains() -> None:
  root = _repo_root()
  paths: list[Path] = []
  for pattern in _PRODUCT_GLOBS:
    paths.extend(root.glob(pattern))
  hits: list[str] = []
  for path in sorted(set(paths)):
    if not path.is_file():
      continue
    if path.name.startswith("test_"):
      continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for tok in _FORBIDDEN:
      if tok in text:
        hits.append(f"{path.relative_to(root)}:{tok}")
        break
  assert not hits, "custom PGO tokens still present:\n" + "\n".join(hits[:40])


def test_pipeline_web_dockerfile_path_layout() -> None:
  """Web/pipeline bake lives under services-conf; root Dockerfile must be gone."""
  root = _repo_root()
  assert (root / "services-conf" / "pipeline_web.Dockerfile").is_file()
  assert not (root / "Dockerfile").exists()


def test_bake_clang_flags_and_enable_optimizations_present() -> None:
  root = _repo_root()
  bake = (root / "services-conf" / "bake_clang_flags.sh").read_text()
  assert "hpcperfstats_bake_cflags" in bake
  assert "hpcperfstats_cpython_make_install" in bake
  assert "--enable-optimizations" in bake
  assert ("f" + "profile-instr") not in bake
  dockerfile = (root / "services-conf" / "pipeline_web.Dockerfile").read_text()
  assert "bake_clang_flags.sh" in dockerfile
  assert "hpcperfstats_cpython_enable_optimizations_for_configure" in dockerfile
  # Upstream --enable-optimizations needs llvm-profdata; keep it without
  # restoring Clang instrumented-profile bake.
  assert "llvm-profdata" in dockerfile
  assert ("f" + "profile-instr") not in dockerfile
