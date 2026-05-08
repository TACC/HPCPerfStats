import {
  EXTENDED_SEARCH_DATE_RANGE_PAIRS,
  EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS,
} from "./extended-search-parameters";

function parseOptionalNumber(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return { empty: true, value: null, invalid: false };
  const n = Number(s);
  if (!Number.isFinite(n)) return { empty: false, value: null, invalid: true };
  return { empty: false, value: n, invalid: false };
}

function parseOptionalDate(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return { empty: true, value: null, invalid: false };
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return { empty: false, value: null, invalid: true };
  const year = Number(m[1]);
  const monthIndex = Number(m[2]) - 1;
  const day = Number(m[3]);
  const date = new Date(Date.UTC(year, monthIndex, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== monthIndex ||
    date.getUTCDate() !== day
  ) {
    return { empty: false, value: null, invalid: true };
  }
  const n = date.getTime();
  return { empty: false, value: n, invalid: false };
}

/**
 * @param {Record<string, string>} params
 * @param {{ metrics?: { metric: string }[] }} options
 */
export function validateExtendedSearchForm(params, options = {}) {
  const invalidHtmlIds = new Set();
  const messages = [];

  const meaningful = Object.entries(params).filter(
    ([, v]) => v != null && String(v).trim() !== "",
  );
  if (meaningful.length === 0) {
    messages.push(
      "Enter at least one search criterion (for example a date, Job ID, host, queue, or numeric threshold).",
    );
    return { ok: false, invalidHtmlIds, messages };
  }

  function pair(gteKey, lteKey, gteId, lteId, label, parseValue = parseOptionalNumber) {
    const a = parseValue(params[gteKey]);
    const b = parseValue(params[lteKey]);
    if (a.invalid) {
      invalidHtmlIds.add(gteId);
      messages.push(`${label} minimum is not valid.`);
    }
    if (b.invalid) {
      invalidHtmlIds.add(lteId);
      messages.push(`${label} maximum is not valid.`);
    }
    if (
      !a.invalid &&
      !b.invalid &&
      !a.empty &&
      !b.empty &&
      a.value > b.value
    ) {
      invalidHtmlIds.add(gteId);
      invalidHtmlIds.add(lteId);
      messages.push(`${label} minimum cannot be greater than maximum.`);
    }
  }

  EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS.forEach((range) => {
    pair(range.gteKey, range.lteKey, range.gteId, range.lteId, range.label);
  });
  EXTENDED_SEARCH_DATE_RANGE_PAIRS.forEach((range) => {
    pair(range.gteKey, range.lteKey, range.gteId, range.lteId, range.label, parseOptionalDate);
  });

  const metrics = options.metrics || [];
  metrics.forEach((m, idx) => {
    const gteKey = `metrics_${m.metric}__gte`;
    const lteKey = `metrics_${m.metric}__lte`;
    pair(gteKey, lteKey, `ext-metric-${idx}-gte`, `ext-metric-${idx}-lte`, m.metric);
  });

  return {
    ok: messages.length === 0,
    invalidHtmlIds,
    messages: [...new Set(messages)],
  };
}
