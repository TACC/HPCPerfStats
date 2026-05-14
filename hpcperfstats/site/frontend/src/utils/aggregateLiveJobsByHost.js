/**
 * Roll up /live/jobs/ rows by host: max CPU/mem, combined usage score, latest ts, distinct jids.
 * @param {Array<{jid?: string, host?: string, cpu_util?: number, mem_util?: number, updated_ts?: number}>} rows
 * @returns {Array<{host: string, usage: number, maxCpu: number, maxMem: number, updatedTs: number, jids: string[]}>}
 */
export function aggregateLiveJobsByHost(rows) {
  if (!Array.isArray(rows)) {
    return [];
  }
  if (rows.length === 0) {
    return [];
  }
  const map = new Map();
  for (const r of rows) {
    const host = typeof r.host === "string" ? r.host.trim() : "";
    if (!host) {
      continue;
    }
    const cpu = Number(r.cpu_util);
    const mem = Number(r.mem_util);
    const cpuOk = Number.isFinite(cpu);
    const memOk = Number.isFinite(mem);
    const rowUsage = Math.max(cpuOk ? cpu : 0, memOk ? mem : 0);
    const ts = Number(r.updated_ts);
    const tsOk = Number.isFinite(ts) ? ts : 0;
    const jid = r.jid != null ? String(r.jid) : "";

    let entry = map.get(host);
    if (!entry) {
      map.set(host, {
        host,
        maxCpu: cpuOk ? cpu : 0,
        maxMem: memOk ? mem : 0,
        usage: rowUsage,
        updatedTs: tsOk,
        jids: new Set(jid ? [jid] : []),
      });
      continue;
    }
    if (cpuOk) {
      entry.maxCpu = Math.max(entry.maxCpu, cpu);
    }
    if (memOk) {
      entry.maxMem = Math.max(entry.maxMem, mem);
    }
    entry.usage = Math.max(entry.usage, rowUsage);
    entry.updatedTs = Math.max(entry.updatedTs, tsOk);
    if (jid) {
      entry.jids.add(jid);
    }
  }
  return Array.from(map.values())
    .map((e) => ({
      host: e.host,
      usage: e.usage,
      maxCpu: e.maxCpu,
      maxMem: e.maxMem,
      updatedTs: e.updatedTs,
      jids: Array.from(e.jids).sort(),
    }))
    .sort((a, b) => b.maxCpu - a.maxCpu || b.usage - a.usage);
}
