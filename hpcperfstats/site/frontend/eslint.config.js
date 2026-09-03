import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import unusedImports from "eslint-plugin-unused-imports";
import reactHooks from "eslint-plugin-react-hooks";

const testImportRestriction = {
  "no-restricted-imports": [
    "error",
    {
      patterns: [
        {
          group: ["**/test/**", "@test/**", "@/test-utils/*", "@/test-fixtures/*"],
          message: "Test-only imports belong in *.test.ts(x) or test/.",
        },
      ],
    },
  ],
};

export default tseslint.config(
  {
    ignores: [
      ".next/**",
      "out/**",
      "public/**",
      "node_modules/**",
      "coverage/**",
      "next-env.d.ts",
      "src/api/generated/**",
      "src/api/generated-zod/**",
      "test/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["app/**/*.{ts,tsx}", "src/**/*.{ts,tsx}", "scripts/**/*.{ts,mts,js,mjs}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      "unused-imports": unusedImports,
      "react-hooks": reactHooks,
    },
    rules: {
      "@typescript-eslint/no-unused-vars": "off",
      "@typescript-eslint/no-explicit-any": "error",
      "unused-imports/no-unused-imports": "error",
      "unused-imports/no-unused-vars": [
        "error",
        {
          vars: "all",
          varsIgnorePattern: "^_",
          args: "after-used",
          argsIgnorePattern: "^_",
        },
      ],
      "no-console": "off",
      "prefer-const": "error",
      "no-unsafe-finally": "error",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "no-restricted-syntax": [
        "error",
        {
          selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
          message: "Avoid dangerouslySetInnerHTML (react/no-danger equivalent).",
        },
      ],
    },
  },
  {
    files: [
      "app/**/*.{ts,tsx}",
      "src/**/*.{ts,tsx}",
      "scripts/**/*.{ts,mts,js,mjs}",
    ],
    ignores: [
      "**/*.test.ts",
      "**/*.test.tsx",
      "**/*.spec.ts",
      "**/*.spec.tsx",
      "scripts/audit-wire-drift.mts",
    ],
    rules: testImportRestriction,
  },
  {
    files: [
      "**/*.test.ts",
      "**/*.test.tsx",
      "**/*.spec.ts",
      "**/*.spec.tsx",
      "test/**/*.{ts,tsx}",
      "scripts/audit-wire-drift.mts",
    ],
    rules: {
      "no-restricted-imports": "off",
    },
  },
);
