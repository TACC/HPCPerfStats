/**
 * Compile-time regression: Next 16.3+ defines ``ImportMetaEnv``; VITEST must
 * remain available for ``isVitestLike()`` in bokeh-embed-defaults.ts.
 * If this file fails typecheck, restore the global ``ImportMetaEnv`` augmentation
 * in ``src/types/global.d.ts`` (declare global, not module-scoped).
 */
type AssertVitestOnImportMetaEnv = ImportMetaEnv extends {
  readonly VITEST?: string;
}
  ? true
  : never;

const importMetaEnvHasVitest: AssertVitestOnImportMetaEnv = true;
void importMetaEnvHasVitest;

export {};
