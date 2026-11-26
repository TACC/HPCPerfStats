"""Regression: frontend-builder Docker stage must not COPY the whole repo before npm build."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _frontend_json(name: str) -> dict:
  return json.loads(
    (_repo_root() / "hpcperfstats/site/frontend" / name).read_text(
      encoding="utf-8"
    )
  )


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
  dockerfile = (
    _repo_root() / "services-conf" / "pipeline_web.Dockerfile"
  ).read_text()
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


_FRONTEND_DEV_ONLY_PACKAGES = frozenset(
  {
    "@bokeh/bokehjs",
    "@tailwindcss/postcss",
    "autoprefixer",
    "shadcn",
    "tailwindcss",
    "tailwindcss-animate",
    "tw-animate-css",
  }
)


def test_frontend_build_tooling_lives_in_dev_dependencies():
  """CLI, PostCSS/Tailwind, and Bokeh vendor copy are build-time — not runtime deps."""
  package_json = _frontend_json("package.json")
  deps = set((package_json.get("dependencies") or {}).keys())
  dev_deps = set((package_json.get("devDependencies") or {}).keys())
  assert not _FRONTEND_DEV_ONLY_PACKAGES & deps, (
    f"build-only packages must not be in dependencies: "
    f"{sorted(_FRONTEND_DEV_ONLY_PACKAGES & deps)}"
  )
  missing = _FRONTEND_DEV_ONLY_PACKAGES - dev_deps
  assert not missing, f"expected devDependencies to include {sorted(missing)}"


def test_frontend_package_json_allowscripts_covers_esbuild():
  """npm 12 blocks dependency install scripts unless allowScripts opts in."""
  package_json = _frontend_json("package.json")
  allow = package_json.get("allowScripts") or {}
  assert allow.get("esbuild@0.28.2") is True


def test_js_yaml_lock_copies_stay_on_v4_for_orval_default_import():
  """Orval does `import jsYaml from "js-yaml"`; js-yaml 5 has no default export.

  Orval pins js-yaml 4.3.2 itself, so there is no override; a 5.x copy or one
  below the GHSA-h67p-54hq-rp68 fix fails.
  """
  _assert_lock_copies_meet_floor(
    "js-yaml",
    {4: (4, 2, 0)},
    reason="Orval default import / GHSA-h67p-54hq-rp68",
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
  package_json = _frontend_json("package.json")
  override = (package_json.get("overrides") or {}).get(package)
  assert override is not None, f"{package} override required ({reason})"
  assert _semver_triple(override) >= min_version, (
    f"{package} override must be >= {min_version[0]}.{min_version[1]}."
    f"{min_version[2]} ({reason}); got {override!r}"
  )
  lock = _frontend_json("package-lock.json")
  locked = (lock.get("packages") or {}).get(f"node_modules/{package}") or {}
  locked_version = locked.get("version")
  assert locked_version, f"package-lock.json missing node_modules/{package}"
  assert _semver_triple(locked_version) >= min_version, (
    f"lock {package}@{locked_version} must be >= "
    f"{min_version[0]}.{min_version[1]}.{min_version[2]} ({reason})"
  )


def _assert_lock_copies_meet_floor(
  package: str,
  floors: dict[int, tuple[int, int, int]],
  *,
  reason: str,
) -> None:
  """Every lockfile copy of ``package`` must meet the floor of its major line.

  For advisories on packages without an override: absence passes, a copy on a
  major line with no listed floor fails, and a copy below its floor fails.
  """
  lock = _frontend_json("package-lock.json")
  for name, pkg in (lock.get("packages") or {}).items():
    if name.endswith(f"node_modules/{package}"):
      version = _semver_triple(pkg.get("version") or "")
      floor = floors.get(version[0])
      assert floor is not None and version >= floor, (
        f"lock {name}@{pkg.get('version')} is not on an allowed line "
        f"at or above its floor {floors} ({reason})"
      )


def test_dompurify_override_meets_dependabot_117_floor():
  """Dependabot #117 / GHSA-p98j-92pf-mc4p: dompurify <= 3.4.15 is vulnerable."""
  _assert_npm_override_floor(
    "dompurify",
    (3, 4, 16),
    reason="GHSA-p98j-92pf-mc4p / Dependabot #117",
  )


def test_nanoid_lock_copies_meet_dependabot_109_floor():
  """Dependabot #109 / GHSA-2v37-7h3g-55p8: nanoid < 3.3.18 is vulnerable (DoS on size 0).

  postcss asks for ``^3`` and @scalar/types for ``^5``, both at patched
  releases without an override (5.x fixed in 5.1.16, GHSA-28wg-ghj8-5hjv).
  """
  _assert_lock_copies_meet_floor(
    "nanoid",
    {3: (3, 3, 18), 5: (5, 1, 16)},
    reason="GHSA-2v37-7h3g-55p8 / GHSA-28wg-ghj8-5hjv / Dependabot #109",
  )


def test_minimatch_lock_copies_meet_dependabot_122_floor():
  """Dependabot #122 / GHSA-g3hv-75wg-m249: minimatch 7.0.0-11.0.0 globParts DoS.

  The fix is 11.x only. Every consumer now accepts 11.x, so there is no
  override; any copy on an older line fails.
  """
  _assert_lock_copies_meet_floor(
    "minimatch",
    {11: (11, 0, 1)},
    reason="GHSA-g3hv-75wg-m249 / Dependabot #122",
  )


def test_hono_lock_copies_meet_dependabot_125_129_floor():
  """Dependabot #125-#129: hono <= 4.13.15 (csrf ping, mount, css, form, router).

  Not in the tree since shadcn 4.23 (``@modelcontextprotocol/server`` has no
  hono dependency), so there is no override. Fixed in 4.13.16.
  """
  _assert_lock_copies_meet_floor(
    "hono",
    {4: (4, 13, 16)},
    reason="GHSA-46wx-v44x-9hh2 et al. / Dependabot #125-#129",
  )


def test_hono_node_server_lock_copies_meet_dependabot_123_floor():
  """Dependabot #123 / GHSA-jpvv-r467-5p4w: @hono/node-server < 2.1.6.

  Backslash in the request path parsed as a separator (proxy ACL bypass).
  Not in the tree since shadcn 4.23, so there is no override.
  """
  _assert_lock_copies_meet_floor(
    "@hono/node-server",
    {2: (2, 1, 6)},
    reason="GHSA-jpvv-r467-5p4w / Dependabot #123",
  )


def test_next_meets_dependabot_130_floor():
  """Dependabot #130 / GHSA-mfr8-8qww-v76h: next 16.3.0-16.4.4 leaks root
  params across ``'use cache'`` calls made through ``unstable_cache()``.

  ``next`` is a direct dependency, so the floor applies to the package.json
  range and the locked version rather than an override.
  """
  floor = (16, 4, 5)
  spec = _frontend_json("package.json")["dependencies"]["next"]
  assert _semver_triple(spec) >= floor, f"next range {spec!r} below 16.4.5"
  lock = _frontend_json("package-lock.json")["packages"]["node_modules/next"]
  assert _semver_triple(lock["version"]) >= floor, (
    f"lock next@{lock['version']} below 16.4.5 (Dependabot #130)"
  )


def test_fast_uri_lock_copies_meet_dependabot_113_116_floor():
  """fast-uri: Dependabot #113-#116 plus the 2026-10 host/scheme GHSAs.

  ajv asks for ``^3``; the 3.x line is fixed in 3.1.9 and 4.x in 4.2.3, so
  there is no override forcing ajv onto 4.x.
  """
  _assert_lock_copies_meet_floor(
    "fast-uri",
    {3: (3, 1, 9), 4: (4, 2, 3)},
    reason="GHSA-w74x-v6f9-j3xg / GHSA-5jgf-p345-68v8",
  )


def test_postcss_lock_copies_meet_ghsa_fxqj_floor():
  """postcss <= 8.5.22: GHSA-fxqj-rqcc-2cmp; next pins 8.5.23 without an override."""
  _assert_lock_copies_meet_floor(
    "postcss",
    {8: (8, 5, 23)},
    reason="GHSA-fxqj-rqcc-2cmp",
  )


def test_undici_lock_copies_meet_2026_10_floor():
  """undici 2026-10 GHSAs (GHSA-4w7w-5xrc-9h26 et al.): fixed in 7.30.1 and 8.12.2."""
  _assert_lock_copies_meet_floor(
    "undici",
    {7: (7, 30, 1), 8: (8, 12, 2)},
    reason="GHSA-4w7w-5xrc-9h26 et al.",
  )


def test_babel_core_lock_copies_meet_ghsa_4x5r_floor():
  """@babel/core <= 7.29.0: GHSA-4x5r-pxfx-6jf8; shadcn and the plugins ask for ``^7``."""
  _assert_lock_copies_meet_floor(
    "@babel/core",
    {7: (7, 29, 6)},
    reason="GHSA-4x5r-pxfx-6jf8",
  )


def test_body_parser_lock_copies_meet_audit_floor():
  """body-parser 2.0.0-2.3.0: GHSA-27r3-jxmh-p828 (trailing bytes DoS).

  Not in the tree since shadcn 4.23, so there is no override.
  """
  _assert_lock_copies_meet_floor(
    "body-parser",
    {2: (2, 4, 0)},
    reason="GHSA-27r3-jxmh-p828",
  )


def test_qs_lock_copies_meet_dependabot_111_112_floor():
  """Dependabot #111-#112 / GHSA-*-qs: qs <= 6.15.3 is vulnerable.

  Not in the tree since shadcn 4.23, so there is no override.
  """
  _assert_lock_copies_meet_floor(
    "qs",
    {6: (6, 16, 0)},
    reason="GHSA-x5fp-wj9c-mxmx / Dependabot #111-#112",
  )


def test_xmldom_override_meets_dependabot_110_floor():
  """Dependabot #110 / GHSA-6gmq-8vp8-gcm6: @xmldom/xmldom <= 0.9.11 is vulnerable."""
  _assert_npm_override_floor(
    "@xmldom/xmldom",
    (0, 9, 12),
    reason="GHSA-6gmq-8vp8-gcm6 / Dependabot #110",
  )


def test_mcp_sdk_lock_meets_dependabot_119_floor():
  """Dependabot #119 / GHSA-6qxp-vccf-f47h: MCP SDK < 1.31.0 is vulnerable.

  shadcn 4.23 depends on ``@modelcontextprotocol/server`` instead; any SDK
  copy in the lock must still meet the floor.
  """
  _assert_lock_copies_meet_floor(
    "@modelcontextprotocol/sdk",
    {1: (1, 31, 0)},
    reason="GHSA-6qxp-vccf-f47h / Dependabot #119",
  )


def test_deepmerge_stays_dev_only_for_dependabot_121():
  """Dependabot #121 / GHSA-gcqx-w48m-w2h4: deepmerge has no patched release.

  Dismissed as tolerable risk only while every lock copy is dev-only (shadcn CLI,
  bokehjs -> choices.js) and the vendored Bokeh bundle is ``bokeh.min.js``, not
  ``bokeh-widgets*.js`` (which inlines choices.js + deepmerge).
  """
  frontend = _repo_root() / "hpcperfstats/site/frontend"
  lock = _frontend_json("package-lock.json")
  entries = {
    name: pkg
    for name, pkg in (lock.get("packages") or {}).items()
    if name.endswith("node_modules/deepmerge")
  }
  assert entries, (
    "package-lock.json missing deepmerge; re-check Dependabot #121"
  )
  runtime = sorted(name for name, pkg in entries.items() if not pkg.get("dev"))
  assert not runtime, (
    f"deepmerge reached runtime deps {runtime}; revisit Dependabot #121 dismissal"
  )
  vendor = (frontend / "scripts/sync-bokeh-vendor.mjs").read_text(
    encoding="utf-8"
  )
  assert '"build/js/bokeh.min.js"' in vendor
  assert "bokeh-widgets" not in vendor


def test_build_prod_runs_write_site_identity_hook():
  """build:prod must generate gitignored site-identity.ts (Docker uses build:prod, not build)."""
  package_json = _frontend_json("package.json")
  scripts = package_json.get("scripts") or {}
  assert scripts.get("prebuild:prod") == "npm run write-site-identity"


def test_rebuild_frontend_node_image_matches_frontend_builder():
  """SPA hot path (rebuild_frontend.sh) must build with the image's Node pin."""
  root = _repo_root()
  dockerfile = (root / "services-conf" / "pipeline_web.Dockerfile").read_text()
  script = (root / "scripts" / "rebuild_frontend.sh").read_text()
  from_match = re.search(
    r"^FROM (node:\S+) AS frontend-builder$", dockerfile, flags=re.MULTILINE
  )
  script_match = re.search(
    r'^NODE_IMAGE="(node:\S+)"$', script, flags=re.MULTILINE
  )
  assert from_match and script_match
  assert re.fullmatch(r"node:\d+\.\d+\.\d+-alpine\d+\.\d+", from_match.group(1))
  assert script_match.group(1) == from_match.group(1)
