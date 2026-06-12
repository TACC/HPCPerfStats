import { describe, expect, it } from "vitest";
import { MONITOR_EVENT_METADATA_LEGACY } from "./variableMetadataMonitorEventsLegacy";

describe("MONITOR_EVENT_METADATA_LEGACY", () => {
  it("documents Intel CTL/CTR register keys", () => {
    expect(MONITOR_EVENT_METADATA_LEGACY.CTL0?.description).toMatch(/select register/i);
    expect(MONITOR_EVENT_METADATA_LEGACY.CTR3?.description).toMatch(/counter value/i);
    expect(MONITOR_EVENT_METADATA_LEGACY.FIXED_CTR0?.description).toMatch(/fixed counter/i);
  });

  it("documents legacy IMC and PMC aliases", () => {
    expect(MONITOR_EVENT_METADATA_LEGACY.CAS_READS?.description).toMatch(/DRAM CAS read/i);
    expect(MONITOR_EVENT_METADATA_LEGACY.INST_RETIRED?.description).toMatch(
      /instructions retired/i,
    );
    expect(MONITOR_EVENT_METADATA_LEGACY.FLOPS?.description).toMatch(/floating-point/i);
  });

  it("keeps legacy memory keys separate from canonical names", () => {
    expect(MONITOR_EVENT_METADATA_LEGACY.MemTotal?.description).toMatch(/memory/i);
    expect(Object.keys(MONITOR_EVENT_METADATA_LEGACY)).not.toContain("instr_retired");
  });
});
