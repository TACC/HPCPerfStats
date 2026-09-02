/**
 * Maps normalized request path + HTTP method to Orval-generated Zod response schemas.
 * Update when OpenAPI adds endpoints (see openapi-orval-sync.mdc).
 */
import type { z } from "zod";
import {
  AdminMonitorRetrieveResponse,
  CacheInvalidatePageCreateResponse,
  SacctIngestCreateResponse,
} from "./generated-zod/admin/admin";
import { HomeRetrieveResponse } from "./generated-zod/home/home";
import {
  JobsHistogramsBatchRetrieveResponse,
  JobsHistogramsRetrieveResponse,
  JobsFilterOptionsRetrieveResponse,
  JobsPlotsRetrieveResponse,
  JobsRetrieve2Response,
  JobsRetrieve3Response,
  JobsRetrieveResponse,
} from "./generated-zod/jobs/jobs";
import { HostPlotRetrieveResponse } from "./generated-zod/hosts/hosts";
import {
  JobMonitorGpuRetrieveResponse,
  JobMonitorRetrieveResponse,
} from "./generated-zod/monitor/monitor";
import { PubClusterDashboardRetrieveResponse } from "./generated-zod/public/public";
import {
  SessionDropStaffCreateResponse,
  SessionRetrieveResponse,
  TestLoginUserCreateResponse,
  TestLoginUserRetrieveResponse,
  UserApiKeyRetrieveResponse,
  UserApiKeyRotateCreateResponse,
} from "./generated-zod/session/session";

type ZodSchema = z.ZodType;

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
  "GET /api/session/": SessionRetrieveResponse,
  "POST /api/session/drop-staff/": SessionDropStaffCreateResponse,
  "GET /api/test-login/user/": TestLoginUserRetrieveResponse,
  "POST /api/test-login/user/": TestLoginUserCreateResponse,
  "GET /api/user-api-key/": UserApiKeyRetrieveResponse,
  "POST /api/user-api-key/rotate/": UserApiKeyRotateCreateResponse,
  "POST /api/cache/invalidate-page/": CacheInvalidatePageCreateResponse,
  "POST /api/sacct/ingest/": SacctIngestCreateResponse,
  "GET /api/home/": HomeRetrieveResponse,
  "GET /api/jobs/": JobsRetrieveResponse,
  "GET /api/jobs/filter_options/": JobsFilterOptionsRetrieveResponse,
  "GET /api/jobs/histograms/": JobsHistogramsRetrieveResponse,
  "GET /api/jobs/histograms/batch/": JobsHistogramsBatchRetrieveResponse,
  "GET /api/host_plot/": HostPlotRetrieveResponse,
  "GET /api/admin_monitor/": AdminMonitorRetrieveResponse,
  "GET /api/job_monitor/gpu/": JobMonitorGpuRetrieveResponse,
  "GET /api/job_monitor/": JobMonitorRetrieveResponse,
  "GET /api/pub/cluster-dashboard/": PubClusterDashboardRetrieveResponse,
};

/** Exposed for wiring drift tests (frontend-stack-wiring-contract.mdc). */
export const EXACT_RESPONSE_SCHEMA_ROUTES = Object.keys(EXACT_RESPONSE_SCHEMAS);

export function resolveResponseSchema(method: string, url: string): ZodSchema | null {
  const upper = method.toUpperCase();
  const path = normalizeApiPath(url);
  const exact = EXACT_RESPONSE_SCHEMAS[`${upper} ${path}`];
  if (exact) return exact;

  if (upper === "GET" && matchJobDetailPlots(path)) return JobsPlotsRetrieveResponse;
  if (upper === "GET" && matchTypeDetail(path)) return JobsRetrieve3Response;
  if (upper === "GET" && matchJobDetail(path)) return JobsRetrieve2Response;
  return null;
}
