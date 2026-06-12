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

  it("does not use scientific notation in descriptions", () => {
    for (const meta of Object.values(MONITOR_EVENT_METADATA)) {
      expect(meta.description).not.toMatch(/e[+-]?\d+/i);
    }
  });
});
