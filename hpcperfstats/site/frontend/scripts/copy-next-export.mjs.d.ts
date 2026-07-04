export declare const PRODUCTION_EXCLUDED_EXPORT_DIRS: readonly string[];

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
