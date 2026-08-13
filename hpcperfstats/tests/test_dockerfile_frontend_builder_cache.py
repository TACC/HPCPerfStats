"""Regression: frontend-builder Docker stage must not COPY the whole repo before npm build."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _frontend_builder_stage(dockerfile: str) -> str:
  match = re.search(
      r"^FROM node:.* AS frontend-builder\s*\n(.*?)(?=^FROM |\Z)",
      dockerfile,
      flags=re.MULTILINE | re.DOTALL,
  )
  assert match, "frontend-builder stage not found in Dockerfile"
  return match.group(1)


def test_frontend_builder_does_not_copy_entire_repo():
  """Backend-only edits must not invalidate the npm ci / build cache layers."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  stage = _frontend_builder_stage(dockerfile)

  assert "COPY --chown=node:node . ." not in stage
  assert "npm ci" in stage
  assert "npm run build:prod" in stage
  assert re.search(r"ARG NPM_VERSION=", stage)
  assert re.search(r'npm install -g ["\']npm@\$\{NPM_VERSION\}["\']', stage)
  assert re.search(
      r"COPY --chown=node:node hpcperfstats/site/frontend/package\.json",
      stage,
  )
  assert "hpcperfstats/site/openapi/openapi.yaml" in stage
  assert re.search(
      r"COPY --chown=node:node hpcperfstats/site/frontend/ \./",
      stage,
  )

  npm_upgrade_pos = stage.index("npm install -g")
  npm_ci_pos = stage.index("npm ci")
  build_pos = stage.index("npm run build:prod")
  frontend_copy_pos = stage.rindex("hpcperfstats/site/frontend/")
  assert npm_upgrade_pos < npm_ci_pos
  assert npm_ci_pos < build_pos
  assert frontend_copy_pos < build_pos


def test_frontend_package_json_allowscripts_covers_esbuild():
  """npm 12 blocks dependency install scripts unless allowScripts opts in."""
  import json

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  allow = package_json.get("allowScripts") or {}
  assert allow.get("esbuild@0.28.1") is True


def test_js_yaml_override_stays_on_v4_for_orval_default_import():
  """Orval does `import jsYaml from "js-yaml"`; js-yaml 5 has no default export."""
  import json
  import re

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  override = (package_json.get("overrides") or {}).get("js-yaml")
  assert override is not None, "js-yaml override required (GHSA-h67p-54hq-rp68 floor)"
  assert re.fullmatch(r"\^?4\.\d+\.\d+", override), (
      f"js-yaml override must stay on 4.x for Orval ESM default import; got {override!r}"
  )


def test_nanoid_override_meets_dependabot_109_floor():
  """Dependabot #109 / GHSA-2v37-7h3g-55p8: nanoid < 3.3.18 is vulnerable (DoS on size 0)."""
  import json
  import re

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  override = (package_json.get("overrides") or {}).get("nanoid")
  assert override is not None, "nanoid override required (GHSA-2v37-7h3g-55p8 / Dependabot #109)"
  match = re.fullmatch(r"\^?(3)\.(\d+)\.(\d+)", override)
  assert match, f"nanoid override must stay on 3.x for postcss; got {override!r}"
  major, minor, patch = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
  assert (major, minor, patch) >= (3, 3, 18), (
      f"nanoid override must be >= 3.3.18 (Dependabot first_patched); got {override!r}"
  )

  lock = (_repo_root() / "hpcperfstats/site/frontend/package-lock.json").read_text(
      encoding="utf-8",
  )
  # Locked resolved entry must not stay on the vulnerable 3.3.17 tarball.
  assert "nanoid/-/nanoid-3.3.17.tgz" not in lock
  assert re.search(r"nanoid/-/nanoid-3\.3\.(1[89]|[2-9]\d)\.tgz", lock), (
      "package-lock.json must resolve nanoid to >= 3.3.18"
  )


def test_build_prod_runs_write_site_identity_hook():
  """build:prod must generate gitignored site-identity.ts (Docker uses build:prod, not build)."""
  import json

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  scripts = package_json.get("scripts") or {}
  assert scripts.get("prebuild:prod") == "npm run write-site-identity"
