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
  assert "npm run build" in stage
  assert re.search(
      r"COPY --chown=node:node hpcperfstats/site/frontend/package\.json",
      stage,
  )
  assert "hpcperfstats/site/openapi/openapi.yaml" in stage
  assert re.search(
      r"COPY --chown=node:node hpcperfstats/site/frontend/ \./",
      stage,
  )

  npm_ci_pos = stage.index("npm ci")
  build_pos = stage.index("npm run build")
  frontend_copy_pos = stage.rindex("hpcperfstats/site/frontend/")
  assert npm_ci_pos < build_pos
  assert frontend_copy_pos < build_pos
