export declare const PRODUCTION_EXCLUDED_EXPORT_DIRS: readonly string[];

export declare function sha256CspHash(content: string): string;

export declare function extractInlineCspHashesFromHtml(html: string): {
  scriptHashes: string[];
  styleHashes: string[];
  styleAttrHashes: string[];
};

export declare function collectInlineCspHashes(rootDir: string): {
  scriptHashes: string[];
  styleHashes: string[];
  styleAttrHashes: string[];
};

export declare function buildNginxCspInclude(options?: {
  scriptHashes?: string[];
  styleHashes?: string[];
  styleAttrHashes?: string[];
  allowUnsafeEval?: boolean;
}): string;

/** Write CSP includes to outDir (private). Never use the public static/frontend tree. */
export declare function writeNginxCspIncludes(htmlRoot: string, outDir?: string): void;

export declare function defaultEdgeNginxCspDir(staticFrontendTarget?: string): string;

export declare function isProductionStaticCopy(
  argv?: string[],
  env?: NodeJS.ProcessEnv,
): boolean;

export declare function copyRecursive(
  src: string,
  dest: string,
  options?: { productionStatic?: boolean },
): void;

export declare function runCopyNextExport(options?: {
  out?: string;
  target?: string;
  productionStatic?: boolean;
  edgeNginxDir?: string;
}): {
  out: string;
  target: string;
  edgeNginxDir: string;
  mode: "production" | "full";
  skipped: string[];
};
