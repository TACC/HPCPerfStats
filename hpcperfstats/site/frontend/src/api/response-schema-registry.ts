/**
 * Maps normalized request path + HTTP method to Orval-generated Zod response schemas.
 * Update when OpenAPI adds endpoints (see openapi-orval-sync.mdc).
 */
import type { z } from "zod";
import {
  adminMonitorRetrieveResponse,
  cacheInvalidatePageCreateResponse,
  sacctIngestCreateResponse,
} from "./generated-zod/admin/admin";
import { homeRetrieveResponse } from "./generated-zod/home/home";
import {
  jobsHistogramsBatchRetrieveResponse,
  jobsHistogramsRetrieveResponse,
  jobsPlotsRetrieveResponse,
  jobsRetrieve2Response,
  jobsRetrieve3Response,
  jobsRetrieveResponse,
} from "./generated-zod/jobs/jobs";
import { hostPlotRetrieveResponse } from "./generated-zod/hosts/hosts";
import {
  jobMonitorGpuRetrieveResponse,
  jobMonitorRetrieveResponse,
} from "./generated-zod/monitor/monitor";
import { pubClusterDashboardRetrieveResponse } from "./generated-zod/public/public";
import {
  sessionDropStaffCreateResponse,
  sessionRetrieveResponse,
  userApiKeyRetrieveResponse,
  userApiKeyRotateCreateResponse,
} from "./generated-zod/session/session";

type ZodSchema = z.ZodTypeAny;

export function normalizeApiPath(url: string): string {
  const withoutQuery = url.split("?")[0];
  if (withoutQuery.startsWith("http://") || withoutQuery.startsWith("https://")) {
    return new URL(withoutQuery).pathname;
  }
  return withoutQuery.startsWith("/") ? withoutQuery : `/${withoutQuery}`;
}

function matchJobDetailPlots(path: string): boolean {
  return /^\/api\/jobs\/[^/]+\/plots\/$/.test(path);
}

function matchJobDetail(path: string): boolean {
  return /^\/api\/jobs\/[^/]+\/$/.test(path);
}

function matchTypeDetail(path: string): boolean {
  return /^\/api\/jobs\/[^/]+\/[^/]+\/$/.test(path) && !path.endsWith("/plots/");
}

/** Registry keyed by `METHOD path` for exact routes; dynamic paths use matchers below. */
const EXACT_RESPONSE_SCHEMAS: Record<string, ZodSchema> = {
  "GET /api/session/": sessionRetrieveResponse,
  "POST /api/session/drop-staff/": sessionDropStaffCreateResponse,
  "GET /api/user-api-key/": userApiKeyRetrieveResponse,
  "POST /api/user-api-key/rotate/": userApiKeyRotateCreateResponse,
  "POST /api/cache/invalidate-page/": cacheInvalidatePageCreateResponse,
  "POST /api/sacct/ingest/": sacctIngestCreateResponse,
  "GET /api/home/": homeRetrieveResponse,
  "GET /api/jobs/": jobsRetrieveResponse,
  "GET /api/jobs/histograms/": jobsHistogramsRetrieveResponse,
  "GET /api/jobs/histograms/batch/": jobsHistogramsBatchRetrieveResponse,
  "GET /api/host_plot/": hostPlotRetrieveResponse,
  "GET /api/admin_monitor/": adminMonitorRetrieveResponse,
  "GET /api/job_monitor/gpu/": jobMonitorGpuRetrieveResponse,
  "GET /api/job_monitor/": jobMonitorRetrieveResponse,
  "GET /api/pub/cluster-dashboard/": pubClusterDashboardRetrieveResponse,
};

/** Exposed for wiring drift tests (frontend-stack-wiring-contract.mdc). */
export const EXACT_RESPONSE_SCHEMA_ROUTES = Object.keys(EXACT_RESPONSE_SCHEMAS);

export function resolveResponseSchema(method: string, url: string): ZodSchema | null {
  const upper = method.toUpperCase();
  const path = normalizeApiPath(url);
  const exact = EXACT_RESPONSE_SCHEMAS[`${upper} ${path}`];
  if (exact) return exact;

  if (upper === "GET" && matchJobDetailPlots(path)) return jobsPlotsRetrieveResponse;
  if (upper === "GET" && matchTypeDetail(path)) return jobsRetrieve3Response;
  if (upper === "GET" && matchJobDetail(path)) return jobsRetrieve2Response;
  return null;
}
