import { defineConfig } from "orval";

export default defineConfig({
  hpcperfstats: {
    input: "../openapi/openapi.yaml",
    output: {
      mode: "tags-split",
      target: "./src/api/generated",
      schemas: "./src/api/generated/models",
      client: "react-query",
      override: {
        mutator: {
          path: "./src/api/fetch-mutator.ts",
          name: "customFetch",
        },
      },
    },
  },
});
