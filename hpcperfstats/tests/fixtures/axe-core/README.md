# Vendored axe-core (test-only)

This directory holds `axe.min.js`, the axe-core build that Playwright Python E2E tests inject from disk.
Docker runtime images do not include `node_modules`, so this file is the supported injection path.
How the accessibility checks use it is in [Check accessibility](../../../../docs/how-to/develop/run-tests.md#check-accessibility).

- Version: 4.14.1 (from npm package `axe-core`)
- License: MPL-2.0 (see <https://github.com/dequelabs/axe-core>)

The SPA's Vitest stack uses `jest-axe` with whatever `axe-core` version is pinned in `hpcperfstats/site/frontend/package-lock.json`, often a slightly older minor than this vendored file.
That split is intentional: browser E2E needs a self-contained file on disk, and unit tests resolve `axe-core` from npm.

To refresh after upgrading, run from the repository root:

```bash
V=<VERSION>
npm pack "axe-core@$V" && \
  tar -xzf "axe-core-$V.tgz" && \
  cp package/axe.min.js hpcperfstats/tests/fixtures/axe-core/
```

`<VERSION>` is the axe-core release to vendor.
