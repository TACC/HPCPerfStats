import { describe, expect, it } from "vitest";
import { aggregateLiveJobsByHost } from "./aggregateLiveJobsByHost";

describe("aggregateLiveJobsByHost", () => {
  it("returns empty array for non-array or empty input", () => {
    expect(aggregateLiveJobsByHost([])).toEqual([]);
    expect(aggregateLiveJobsByHost(null)).toEqual([]);
    expect(aggregateLiveJobsByHost(undefined)).toEqual([]);
  });

  it("aggregates single row", () => {
    const out = aggregateLiveJobsByHost([
      {
        jid: "101",
        host: "n1.cluster.example.com",
        cpu_util: 40,
        mem_util: 55,
        updated_ts: 1000,
      },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      host: "n1.cluster.example.com",
      usage: 55,
      maxCpu: 40,
      maxMem: 55,
      updatedTs: 1000,
      jids: ["101"],
    });
  });

  it("merges multiple rows for same host with max usage, max ts, distinct jids", () => {
    const out = aggregateLiveJobsByHost([
      {
        jid: "1",
        host: "a.example.com",
        cpu_util: 10,
        mem_util: 20,
        updated_ts: 100,
      },
      {
        jid: "2",
        host: "a.example.com",
        cpu_util: 80,
        mem_util: 5,
        updated_ts: 200,
      },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].usage).toBe(80);
    expect(out[0].maxCpu).toBe(80);
    expect(out[0].maxMem).toBe(20);
    expect(out[0].updatedTs).toBe(200);
    expect(out[0].jids).toEqual(["1", "2"]);
  });

  it("deduplicates jid on same host", () => {
    const out = aggregateLiveJobsByHost([
      {
        jid: "9",
        host: "h.example.com",
        cpu_util: 1,
        mem_util: 2,
        updated_ts: 1,
      },
      {
        jid: "9",
        host: "h.example.com",
        cpu_util: 3,
        mem_util: 4,
        updated_ts: 2,
      },
    ]);
    expect(out[0].jids).toEqual(["9"]);
    expect(out[0].maxCpu).toBe(3);
    expect(out[0].maxMem).toBe(4);
  });

  it("skips rows with empty host", () => {
    const out = aggregateLiveJobsByHost([
      { jid: "1", host: "", cpu_util: 50, mem_util: 50, updated_ts: 1 },
      { jid: "2", host: "ok.example.com", cpu_util: 1, mem_util: 1, updated_ts: 1 },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].host).toBe("ok.example.com");
  });

  it("sorts hosts by maxCpu descending", () => {
    const out = aggregateLiveJobsByHost([
      { jid: "a", host: "low.example.com", cpu_util: 10, mem_util: 90, updated_ts: 1 },
      { jid: "b", host: "high.example.com", cpu_util: 90, mem_util: 5, updated_ts: 1 },
      { jid: "c", host: "mid.example.com", cpu_util: 50, mem_util: 40, updated_ts: 1 },
    ]);
    expect(out.map((x) => x.host)).toEqual([
      "high.example.com",
      "mid.example.com",
      "low.example.com",
    ]);
  });
});
