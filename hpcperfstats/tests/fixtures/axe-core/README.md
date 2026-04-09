# Vendored axe-core (test-only)

- **Version:** 4.11.2 (from npm package `axe-core`)
- **License:** MPL-2.0 (see <https://github.com/dequelabs/axe-core>)
- **Purpose:** Playwright Python E2E loads `axe.min.js` from disk; Docker runtime images do not include `node_modules`, so this file is the supported injection path.

To refresh after upgrading:

```bash
npm pack axe-core@<version> && tar -xzf axe-core-<version>.tgz && cp package/axe.min.js hpcperfstats/tests/fixtures/axe-core/
```
