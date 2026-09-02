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


def _semver_triple(spec: str) -> tuple[int, int, int]:
  """Parse ``^X.Y.Z`` / ``X.Y.Z`` override specs into a comparable triple."""
  match = re.fullmatch(r"\^?(\d+)\.(\d+)\.(\d+)", spec)
  assert match, f"override spec must be X.Y.Z or ^X.Y.Z; got {spec!r}"
  return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _assert_npm_override_floor(
    package: str,
    min_version: tuple[int, int, int],
    *,
    reason: str,
) -> None:
  """Require package.json override + lockfile version at or above min_version."""
  import json

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(
          encoding="utf-8"
      ),
  )
  override = (package_json.get("overrides") or {}).get(package)
  assert override is not None, f"{package} override required ({reason})"
  assert _semver_triple(override) >= min_version, (
      f"{package} override must be >= {min_version[0]}.{min_version[1]}."
      f"{min_version[2]} ({reason}); got {override!r}"
  )
  lock = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package-lock.json").read_text(
          encoding="utf-8",
      )
  )
  locked = (lock.get("packages") or {}).get(f"node_modules/{package}") or {}
  locked_version = locked.get("version")
  assert locked_version, f"package-lock.json missing node_modules/{package}"
  assert _semver_triple(locked_version) >= min_version, (
      f"lock {package}@{locked_version} must be >= "
      f"{min_version[0]}.{min_version[1]}.{min_version[2]} ({reason})"
  )


def test_nanoid_override_meets_dependabot_109_floor():
  """Dependabot #109 / GHSA-2v37-7h3g-55p8: nanoid < 3.3.18 is vulnerable (DoS on size 0)."""
  _assert_npm_override_floor(
      "nanoid",
      (3, 3, 18),
      reason="GHSA-2v37-7h3g-55p8 / Dependabot #109",
  )


def test_fast_uri_override_meets_dependabot_113_116_floor():
  """Dependabot #113–#116 / GHSA-*-fast-uri: fast-uri < 4.1.3 is vulnerable."""
  _assert_npm_override_floor(
      "fast-uri",
      (4, 1, 3),
      reason="GHSA-5jgf-p345-68v8 / Dependabot #113–#116",
  )


def test_qs_override_meets_dependabot_111_112_floor():
  """Dependabot #111–#112 / GHSA-*-qs: qs <= 6.15.3 is vulnerable."""
  _assert_npm_override_floor(
      "qs",
      (6, 16, 0),
      reason="GHSA-x5fp-wj9c-mxmx / Dependabot #111–#112",
  )


def test_xmldom_override_meets_dependabot_110_floor():
  """Dependabot #110 / GHSA-6gmq-8vp8-gcm6: @xmldom/xmldom <= 0.9.11 is vulnerable."""
  _assert_npm_override_floor(
      "@xmldom/xmldom",
      (0, 9, 12),
      reason="GHSA-6gmq-8vp8-gcm6 / Dependabot #110",
  )


def test_build_prod_runs_write_site_identity_hook():
  """build:prod must generate gitignored site-identity.ts (Docker uses build:prod, not build)."""
  import json

  package_json = json.loads(
      (_repo_root() / "hpcperfstats/site/frontend/package.json").read_text(encoding="utf-8"),
  )
  scripts = package_json.get("scripts") or {}
  assert scripts.get("prebuild:prod") == "npm run write-site-identity"
