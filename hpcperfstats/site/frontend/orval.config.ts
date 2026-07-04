import { defineConfig } from "orval";

const input = "../openapi/openapi.yaml";

export default defineConfig({
  hpcperfstatsZod: {
    input,
    output: {
      mode: "tags-split",
      target: "./src/api/generated-zod",
      client: "zod",
      override: {
        zod: {
          version: 4,
          dateTimeOptions: {
            local: true,
            offset: true,
          },
          generate: {
            response: true,
            body: true,
            query: true,
            param: true,
            header: true,
          },
        },
      },
    },
  },
  hpcperfstats: {
    input,
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
