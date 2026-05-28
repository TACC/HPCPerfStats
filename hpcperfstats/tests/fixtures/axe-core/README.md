# Vendored axe-core (test-only)

- **Version:** 4.10.2 (from npm package `axe-core`)
- **License:** MPL-2.0 (see <https://github.com/dequelabs/axe-core>)
- **Purpose:** Playwright Python E2E loads `axe.min.js` from disk; Docker runtime images do not include `node_modules`, so this file is the supported injection path.

**Version note:** the SPA’s **Vitest** stack uses **`jest-axe`** with whatever **`axe-core`** version is pinned in `hpcperfstats/site/frontend/package-lock.json` (often a slightly older minor than this vendored file). That split is intentional: browser E2E needs a self-contained file on disk; unit tests resolve `axe-core` from npm.

To refresh after upgrading:

```bash
npm pack axe-core@<version> && tar -xzf axe-core-<version>.tgz && cp package/axe.min.js hpcperfstats/tests/fixtures/axe-core/
```
