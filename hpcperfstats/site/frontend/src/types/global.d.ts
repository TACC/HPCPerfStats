import type { BokehJS } from "@bokeh/bokehjs";

interface ImportMetaEnv {
  readonly MODE: string;
  readonly DEV: boolean;
  readonly PROD: boolean;
  readonly VITEST?: string;
}

declare global {
  interface Window {
    Bokeh?: BokehJS;
    /** Set by patch-resize-observer-for-bokeh.ts when deferral is active. */
    __hpcperfstatsResizeObserverDeferred?: boolean;
    __HPCPERFSTATS_BOKEH_SMOKE_READY__?: boolean;
    __HPCPERFSTATS_NEXT_BOKEH__?: typeof import("@bokeh/bokehjs");
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}

export {};
