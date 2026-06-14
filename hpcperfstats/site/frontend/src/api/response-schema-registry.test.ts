import { describe, expect, it } from "vitest";
import {
  EXACT_RESPONSE_SCHEMA_ROUTES,
  normalizeApiPath,
  resolveResponseSchema,
} from "./response-schema-registry";

/** Keep in sync with scripts/audit-wire-drift.mts exact routes. */
const EXPECTED_EXACT_ROUTES = [
  "GET /api/session/",
  "POST /api/session/drop-staff/",
  "GET /api/user-api-key/",
  "POST /api/user-api-key/rotate/",
  "POST /api/cache/invalidate-page/",
  "POST /api/sacct/ingest/",
  "GET /api/home/",
  "GET /api/jobs/",
  "GET /api/jobs/histograms/",
  "GET /api/jobs/histograms/batch/",
  "GET /api/host_plot/",
  "GET /api/admin_monitor/",
  "GET /api/job_monitor/gpu/",
  "GET /api/job_monitor/",
  "GET /api/pub/cluster-dashboard/",
];

const DYNAMIC_ROUTE_CASES = [
  { method: "GET", url: "/api/jobs/991/", label: "GET /api/jobs/{id}/" },
  { method: "GET", url: "/api/jobs/991/plots/", label: "GET /api/jobs/{id}/plots/" },
  {
    method: "GET",
    url: "/api/jobs/991/cpu/",
    label: "GET /api/jobs/{jid}/{type}/",
  },
];

describe("response-schema-registry wiring", () => {
  it("registers all exact routes expected by audit-wire-drift", () => {
    expect([...EXACT_RESPONSE_SCHEMA_ROUTES].sort()).toEqual(
      [...EXPECTED_EXACT_ROUTES].sort(),
    );
  });

  it("resolves dynamic job detail routes", () => {
    for (const { method, url } of DYNAMIC_ROUTE_CASES) {
      expect(resolveResponseSchema(method, url)).not.toBeNull();
    }
  });

  it("normalizes absolute URLs to pathname keys", () => {
    expect(normalizeApiPath("https://cluster.example/api/jobs/?page=2")).toBe(
      "/api/jobs/",
    );
  });

  it("returns null for unregistered routes", () => {
    expect(resolveResponseSchema("GET", "/api/unregistered/")).toBeNull();
  });
});
