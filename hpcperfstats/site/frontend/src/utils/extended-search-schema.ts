import { z } from "zod";
import {
  EXTENDED_SEARCH_ALLOWED_PARAM_NAMES,
  EXTENDED_SEARCH_DATE_RANGE_PAIRS,
  EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS,
  getExtendedSearchParameterDefinition,
} from "./extended-search-parameters";

const METRIC_FILTER_KEY = /^metrics_[a-zA-Z0-9_.-]+__(?:gte|lte)$/;

function optionalNumber(raw: unknown) {
  const s = String(raw ?? "").trim();
  if (!s) return { empty: true as const, value: null, invalid: false as const };
  const n = Number(s);
  if (!Number.isFinite(n)) return { empty: false as const, value: null, invalid: true as const };
  return { empty: false as const, value: n, invalid: false as const };
}

function optionalDate(raw: unknown) {
  const s = String(raw ?? "").trim();
  if (!s) return { empty: true as const, value: null, invalid: false as const };
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return { empty: false as const, value: null, invalid: true as const };
  const year = Number(m[1]);
  const monthIndex = Number(m[2]) - 1;
  const day = Number(m[3]);
  const date = new Date(Date.UTC(year, monthIndex, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== monthIndex ||
    date.getUTCDate() !== day
  ) {
    return { empty: false as const, value: null, invalid: true as const };
  }
  return { empty: false as const, value: date.getTime(), invalid: false as const };
}

export type ExtendedSearchFormValues = Record<string, string>;

export type ExtendedSearchMetricOption = { metric: string; units?: string };

export function buildExtendedSearchZodSchema(metrics: ExtendedSearchMetricOption[] = []) {
  return z
    .record(z.string(), z.string().optional())
    .superRefine((params, ctx) => {
      for (const [key, raw] of Object.entries(params)) {
        if (raw == null || String(raw).trim() === "") continue;
        if (EXTENDED_SEARCH_ALLOWED_PARAM_NAMES.includes(key)) continue;
        if (METRIC_FILTER_KEY.test(key)) continue;
        const def = getExtendedSearchParameterDefinition(key);
        ctx.addIssue({
          code: "custom",
          message: `Unknown search field: ${key}`,
          path: [def?.htmlId ?? key],
        });
      }

      const meaningful = Object.entries(params).filter(
        ([, v]) => v != null && String(v).trim() !== "",
      );
      if (meaningful.length === 0) {
        ctx.addIssue({
          code: "custom",
          message:
            "Enter at least one search criterion (for example a date, Job ID, host, queue, or numeric threshold).",
        });
        return;
      }

      function pair(
        gteKey: string,
        lteKey: string,
        gteId: string,
        lteId: string,
        label: string,
        parseValue: (raw: unknown) => ReturnType<typeof optionalNumber> = optionalNumber,
      ) {
        const a = parseValue(params[gteKey]);
        const b = parseValue(params[lteKey]);
        if (a.invalid) {
          ctx.addIssue({ code: "custom", message: `${label} minimum is not valid.`, path: [gteId] });
        }
        if (b.invalid) {
          ctx.addIssue({ code: "custom", message: `${label} maximum is not valid.`, path: [lteId] });
        }
        if (!a.invalid && !b.invalid && !a.empty && !b.empty && a.value! > b.value!) {
          ctx.addIssue({
            code: "custom",
            message: `${label} minimum cannot be greater than maximum.`,
            path: [gteId],
          });
          ctx.addIssue({
            code: "custom",
            message: `${label} minimum cannot be greater than maximum.`,
            path: [lteId],
          });
        }
      }

      for (const range of EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS) {
        pair(range.gteKey, range.lteKey, range.gteId, range.lteId, range.label);
      }
      for (const range of EXTENDED_SEARCH_DATE_RANGE_PAIRS) {
        pair(range.gteKey, range.lteKey, range.gteId, range.lteId, range.label, optionalDate);
      }
      metrics.forEach((m, idx) => {
        pair(
          `metrics_${m.metric}__gte`,
          `metrics_${m.metric}__lte`,
          `ext-metric-${idx}-gte`,
          `ext-metric-${idx}-lte`,
          m.metric,
        );
      });
    });
}

/** Map Zod issues to legacy validateExtendedSearchForm shape for tests. */
export function zodIssuesToValidationResult(
  issues: z.ZodIssue[],
): { ok: boolean; invalidHtmlIds: Set<string>; messages: string[] } {
  const invalidHtmlIds = new Set<string>();
  const messages: string[] = [];
  for (const issue of issues) {
    if (issue.message) messages.push(issue.message);
    const id = issue.path[0];
    if (typeof id === "string") invalidHtmlIds.add(id);
  }
  return { ok: messages.length === 0, invalidHtmlIds, messages: [...new Set(messages)] };
}
