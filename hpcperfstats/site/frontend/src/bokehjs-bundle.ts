/**
 * Bokeh load path for Next/Turbopack (3.10+).
 *
 * Package ``main`` (``build/js/lib/bokeh.js``) uses bare imports Turbopack cannot
 * resolve. Importing ``bokeh.esm.min.js`` (or ``export *`` from ``lib/**``) still
 * lets Turbopack rewrite the graph and split ``default_resolver``, so
 * ``embed_item`` fails with ``could not resolve type 'Grid'`` /
 * ``DocumentConfig``.
 *
 * Load the **stock UMD** ``bokeh.min.js`` as an unprocessed same-origin script
 * (synced to ``public/vendor/`` by ``scripts/sync-bokeh-vendor.mjs``). That
 * matches the CDN Playwright path and keeps a single registered model graph.
 *
 * See ``bokeh-version-and-vendor-patch-upgrade.mdc``.
 */

/** Runtime shape set on ``window.Bokeh`` by the UMD build. */
export type HpcperfstatsBokehRuntime = {
  version: string;
  embed: { embed_item: (item: unknown, target?: string) => unknown };
  Models: { get?: (name: string) => unknown } & Record<string, unknown>;
  index: unknown;
  protocol: unknown;
  logger: unknown;
  set_log_level: unknown;
  settings: unknown;
  documents: unknown;
  safely: unknown;
};

/**
 * Same-origin URL candidates for the vendored UMD file.
 * Static export + nginx serve ``public/`` under ``assetPrefix``; ``next dev``
 * may expose ``public/`` at the unprefixed ``/vendor/…`` path.
 */
export const BOKEH_VENDOR_SCRIPT_CANDIDATES = [
  "/static/frontend/vendor/bokeh.min.js",
  "/vendor/bokeh.min.js",
] as const;

/** Canonical production path (asserted by Vitest / Playwright static serve). */
export const BOKEH_VENDOR_SCRIPT_PATH = BOKEH_VENDOR_SCRIPT_CANDIDATES[0];

let loadPromise: Promise<HpcperfstatsBokehRuntime> | null = null;

function resolveWindowBokeh(): HpcperfstatsBokehRuntime | null {
  const Bokeh = window.Bokeh;
  if (Bokeh?.embed?.embed_item) {
    return Bokeh;
  }
  return null;
}

/**
 * Inject the vendored UMD script once and resolve ``window.Bokeh``.
 *
 * @returns Registered Bokeh namespace (models already registered by UMD).
 */
export function loadBokehRuntime(): Promise<HpcperfstatsBokehRuntime> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Bokeh requires a browser window"));
  }
  const existing = resolveWindowBokeh();
  if (existing) {
    return Promise.resolve(existing);
  }
  if (!loadPromise) {
    loadPromise = new Promise((resolve, reject) => {
      const tryCandidate = (index: number) => {
        if (index >= BOKEH_VENDOR_SCRIPT_CANDIDATES.length) {
          reject(
            new Error(
              `Failed to load Bokeh UMD from ${BOKEH_VENDOR_SCRIPT_CANDIDATES.join(" or ")}`,
            ),
          );
          return;
        }
        const src = BOKEH_VENDOR_SCRIPT_CANDIDATES[index];
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.dataset.hpcperfstatsBokehVendor = "1";
        script.onload = () => {
          const Bokeh = resolveWindowBokeh();
          if (!Bokeh) {
            reject(new Error(`Bokeh UMD loaded from ${src} but window.Bokeh.embed missing`));
            return;
          }
          resolve(Bokeh);
        };
        script.onerror = () => {
          script.remove();
          tryCandidate(index + 1);
        };
        document.head.appendChild(script);
      };
      tryCandidate(0);
    });
  }
  return loadPromise;
}
