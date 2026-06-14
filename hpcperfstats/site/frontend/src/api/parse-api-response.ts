/**
 * Runtime validation for Orval-generated API responses at the customFetch boundary.
 * Maps normalized request path + HTTP method to generated Zod schemas.
 */
import type { z } from "zod";
import {
  cacheInvalidatePageCreateResponse,
  sacctIngestCreateResponse,
} from "./generated-zod/admin/admin";
import { homeRetrieveResponse } from "./generated-zod/home/home";
import {
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

function normalizePath(url: string): string {
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

function resolveResponseSchema(method: string, url: string): ZodSchema | null {
  const upper = method.toUpperCase();
  const path = normalizePath(url);

  if (upper === "GET" && path === "/api/session/") return sessionRetrieveResponse;
  if (upper === "POST" && path === "/api/session/drop-staff/") {
    return sessionDropStaffCreateResponse;
  }
  if (upper === "GET" && path === "/api/user-api-key/") return userApiKeyRetrieveResponse;
  if (upper === "POST" && path === "/api/user-api-key/rotate/") {
    return userApiKeyRotateCreateResponse;
  }
  if (upper === "POST" && path === "/api/cache/invalidate-page/") {
    return cacheInvalidatePageCreateResponse;
  }
  if (upper === "POST" && path === "/api/sacct/ingest/") return sacctIngestCreateResponse;
  if (upper === "GET" && path === "/api/home/") return homeRetrieveResponse;
  if (upper === "GET" && path === "/api/jobs/") return jobsRetrieveResponse;
  if (upper === "GET" && path === "/api/jobs/histograms/") {
    return jobsHistogramsRetrieveResponse;
  }
  if (upper === "GET" && matchJobDetailPlots(path)) return jobsPlotsRetrieveResponse;
  if (upper === "GET" && matchTypeDetail(path)) return jobsRetrieve3Response;
  if (upper === "GET" && matchJobDetail(path)) return jobsRetrieve2Response;
  if (upper === "GET" && path === "/api/host_plot/") return hostPlotRetrieveResponse;
  if (upper === "GET" && path === "/api/admin_monitor/") return adminMonitorRetrieveResponse;
  if (upper === "GET" && path === "/api/job_monitor/gpu/") {
    return jobMonitorGpuRetrieveResponse;
  }
  if (upper === "GET" && path === "/api/job_monitor/") return jobMonitorRetrieveResponse;
  if (upper === "GET" && path === "/api/pub/cluster-dashboard/") {
    return pubClusterDashboardRetrieveResponse;
  }
  return null;
}

export function parseApiResponse<T>(
  method: string,
  url: string,
  payload: unknown,
): T {
  const schema = resolveResponseSchema(method, url);
  if (!schema) return payload as T;
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new Error("API response validation failed");
  }
  return parsed.data as T;
}
