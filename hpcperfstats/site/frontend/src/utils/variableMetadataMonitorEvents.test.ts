import { describe, expect, it } from "vitest";
import { MONITOR_EVENT_METADATA } from "./variableMetadataMonitorEvents";

describe("MONITOR_EVENT_METADATA", () => {
  it("maps every event key to a non-empty description", () => {
    for (const [key, meta] of Object.entries(MONITOR_EVENT_METADATA)) {
      expect(typeof key).toBe("string");
      expect(key.length).toBeGreaterThan(0);
      expect(meta?.description).toEqual(expect.any(String));
      expect(meta.description.trim().length).toBeGreaterThan(0);
    }
  });

  it("includes canonical monitor events used across analysis", () => {
    expect(MONITOR_EVENT_METADATA.instr_retired?.description).toBeTruthy();
    expect(MONITOR_EVENT_METADATA.dram_cas_reads?.description).toBeTruthy();
    expect(MONITOR_EVENT_METADATA.READ_ops?.description).toMatch(/NFS READ/i);
  });

  it("documents Grace DCGM fail-soft + LIKWID overlay for cycle keys", () => {
    const mperf = MONITOR_EVENT_METADATA.mperf?.description ?? "";
    const aperf = MONITOR_EVENT_METADATA.aperf?.description ?? "";
    const est = MONITOR_EVENT_METADATA.cpu_clock_est_cycles?.description ?? "";
    expect(mperf).toMatch(/reference cycles/i);
    expect(mperf).toMatch(/clock_khz/i);
    expect(mperf).toMatch(/LIKWID overlay/i);
    expect(aperf).toMatch(/active cycles/i);
    expect(aperf).toMatch(/util_total/i);
    expect(aperf).toMatch(/LIKWID overlay/i);
    expect(est).toMatch(/active cycles/i);
    expect(est).toMatch(/aperf/i);
    expect(est).toMatch(/LIKWID overlay/i);
    expect(est).not.toMatch(/PAPI may overwrite/i);
  });

  it("does not use scientific notation in descriptions", () => {
    for (const meta of Object.values(MONITOR_EVENT_METADATA)) {
      expect(meta.description).not.toMatch(/e[+-]?\d+/i);
    }
  });
});
