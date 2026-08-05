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

export declare function writeNginxCspIncludes(target: string): void;

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
}): {
  out: string;
  target: string;
  mode: "production" | "full";
  skipped: string[];
};
