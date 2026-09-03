import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(frontendRoot, "./src"),
      "@test": path.resolve(frontendRoot, "./test"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./test/vitest/setupTests.ts",
    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "app/**/*.{test,spec}.{ts,tsx}",
      "scripts/**/*.test.ts",
      "test/**/*.test.{ts,tsx}",
    ],
    pool: "threads",
    maxWorkers: 1,
    fileParallelism: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "test/**",
        "src/api/generated/**",
        "src/utils/generate-variable-metadata-monitor-events.py",
      ],
    },
  },
});
