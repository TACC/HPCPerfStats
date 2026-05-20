import { describe, expect, it } from "vitest";
import { mergeHeatmapHostsWithLive } from "./mergeHeatmapHostsWithLive";

describe("mergeHeatmapHostsWithLive", () => {
  const known = [
    { host: "n1.cluster.example.com", last_time: "2026-05-11T12:00:00+00:00" },
    { host: "n2.cluster.example.com", last_time: "2026-05-11T12:00:00+00:00" },
  ];

  it("returns grey idle cells for known hosts without live rows", () => {
    const out = mergeHeatmapHostsWithLive(known, []);
    expect(out).toHaveLength(2);
    expect(out.every((e) => !e.isLive)).toBe(true);
    expect(out[0].maxCpu).toBe(0);
  });

  it("marks host live when live roll-up matches FQDN", () => {
    const live = [
      {
        host: "n1.cluster.example.com",
        usage: 50,
        maxCpu: 40,
        maxMem: 50,
        updatedTs: 1000,
        jids: ["101"],
      },
    ];
    const out = mergeHeatmapHostsWithLive(known, live);
    const n1 = out.find((e) => e.host === "n1.cluster.example.com");
    expect(n1.isLive).toBe(true);
    expect(n1.maxCpu).toBe(40);
    const n2 = out.find((e) => e.host === "n2.cluster.example.com");
    expect(n2.isLive).toBe(false);
  });
});
