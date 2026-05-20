function shortHostname(fqdn) {
  if (!fqdn || typeof fqdn !== "string") {
    return "";
  }
  const i = fqdn.indexOf(".");
  return i === -1 ? fqdn : fqdn.slice(0, i);
}

/**
 * Merge known hosts (host_data) with live /live/jobs/ roll-up per host.
 * Hosts without live telemetry are marked isLive: false (grey cells).
 *
 * @param {Array<{host?: string, last_time?: string, age_bucket?: string}>} knownHostStats
 * @param {Array<{host: string, usage: number, maxCpu: number, maxMem: number, updatedTs: number, jids: string[]}>} liveByHost
 */
export function mergeHeatmapHostsWithLive(knownHostStats, liveByHost) {
  const knownRows = (knownHostStats || []).filter(
    (h) => h && typeof h.host === "string" && h.host.includes("."),
  );
  const liveMap = new Map(
    liveByHost.map((e) => [e.host, { ...e, isLive: true }]),
  );
  const consumedLiveFqdns = new Set();
  const out = [];

  function liveEntryForKnownFqdn(knownFqdn) {
    const exact = liveMap.get(knownFqdn);
    if (exact) {
      return exact;
    }
    const short = shortHostname(knownFqdn);
    const candidates = liveByHost.filter(
      (e) => shortHostname(e.host) === short,
    );
    if (candidates.length === 1) {
      return { ...candidates[0], isLive: true };
    }
    return null;
  }

  const sortedKnown = [...knownRows].sort((a, b) =>
    shortHostname(a.host).localeCompare(shortHostname(b.host)),
  );
  for (const row of sortedKnown) {
    const live = liveEntryForKnownFqdn(row.host);
    if (live) {
      consumedLiveFqdns.add(live.host);
      out.push({ ...live, adminMeta: row });
    } else {
      out.push({
        host: row.host,
        usage: 0,
        maxCpu: 0,
        maxMem: 0,
        updatedTs: 0,
        jids: [],
        isLive: false,
        adminMeta: row,
      });
    }
  }

  const extras = liveByHost
    .filter((e) => !consumedLiveFqdns.has(e.host))
    .map((e) => ({ ...e, isLive: true }));
  extras.sort((a, b) => b.maxCpu - a.maxCpu || b.usage - a.usage);
  out.push(...extras);

  return out;
}
