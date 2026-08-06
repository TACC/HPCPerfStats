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


def test_build_prod_runs_write_site_identity_hook():
  """build:prod must generate gitignored site-identity.ts (Docker uses build:prod, not build)."""
  import json

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  scripts = package_json.get("scripts") or {}
  assert scripts.get("prebuild:prod") == "npm run write-site-identity"
