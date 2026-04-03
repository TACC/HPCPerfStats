function parseOptionalNumber(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return { empty: true, value: null, invalid: false };
  const n = Number(s);
  if (!Number.isFinite(n)) return { empty: false, value: null, invalid: true };
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

  function pair(gteKey, lteKey, gteId, lteId, label) {
    const a = parseOptionalNumber(params[gteKey]);
    const b = parseOptionalNumber(params[lteKey]);
    if (a.invalid) {
      invalidHtmlIds.add(gteId);
      messages.push(`${label} minimum is not a valid number.`);
    }
    if (b.invalid) {
      invalidHtmlIds.add(lteId);
      messages.push(`${label} maximum is not a valid number.`);
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

  pair("runtime__gte", "runtime__lte", "ext-runtime-gte", "ext-runtime-lte", "Runtime");
  pair("nhosts__gte", "nhosts__lte", "ext-nhosts-gte", "ext-nhosts-lte", "Node count");
  pair("node_hrs__gte", "node_hrs__lte", "ext-node-hrs-gte", "ext-node-hrs-lte", "Node-hours");

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
