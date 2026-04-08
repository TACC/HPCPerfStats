import { describe, expect, it } from "vitest";
import {
  getDescriptionForVariable,
  getVariableTooltipContent,
  normalizeVariableKey,
  VARIABLE_METADATA,
} from "./variableMetadata";

describe("normalizeVariableKey", () => {
  it("strips units suffix in brackets", () => {
    expect(normalizeVariableKey("avg_cpuusage [#cores]")).toBe("avg_cpuusage");
  });

  it("returns bare metric names unchanged", () => {
    expect(normalizeVariableKey("mem_hwm")).toBe("mem_hwm");
  });
});

describe("getVariableTooltipContent", () => {
  it("returns researcherUse for metrics documented in the researcher guide", () => {
    const cpu = getVariableTooltipContent("avg_cpuusage");
    expect(cpu?.description).toMatch(/CPU cores busy/i);
    expect(cpu?.researcherUse).toMatch(/parallel efficiency|OpenMP|MPI/i);
    const jid = getVariableTooltipContent("jid");
    expect(jid?.researcherUse).toEqual(
      expect.stringMatching(/logs|tickets/i),
    );
  });

  it("returns null researcherUse when only a technical definition exists", () => {
    const ev = getVariableTooltipContent("CAS_READS");
    expect(ev?.description).toBeTruthy();
    expect(ev?.researcherUse).toBeNull();
  });
});

describe("getDescriptionForVariable", () => {
  it("appends researcher guidance after an em-dash separator when present", () => {
    const text = getDescriptionForVariable("runtime");
    expect(text).toMatch(/Elapsed runtime/);
    expect(text).toMatch(/\n\n—\n\n/);
    expect(text).toMatch(/requested time|timing out/i);
  });

  it("returns a description for a documented metric", () => {
    expect(getDescriptionForVariable("avg_cpuusage")).toBeTruthy();
    expect(getDescriptionForVariable("mem_hwm")).toBeTruthy();
    expect(getDescriptionForVariable("avg_fabric_mb_per_gflops")).toMatch(
      /fabric bandwidth/i,
    );
    expect(getDescriptionForVariable("flops_node_imbalance")).toMatch(/FLOP/i);
    expect(getDescriptionForVariable("avg_tensor_active")).toMatch(/tensor/i);
    expect(getDescriptionForVariable("dram_bw_node_imbalance")).toMatch(/DRAM/i);
    expect(getDescriptionForVariable("max_node_power_est_w")).toMatch(/node power/i);
    expect(getDescriptionForVariable("avg_node_power_est_w")).toMatch(/Mean estimated on-node power/i);
  });

  it("documents shared filesystem metrics with the job detail section", () => {
    expect(getDescriptionForVariable("avg_sharedfs_bw")).toMatch(
      /Shared File System section/i,
    );
    expect(getDescriptionForVariable("avg_sharedfs_iops")).toMatch(
      /Shared File System section/i,
    );
    expect(getDescriptionForVariable("detail_fsio_llite_read_mb")).toMatch(
      /Lustre llite/i,
    );
    expect(getDescriptionForVariable("detail_fsio_nfs_read_mb")).toMatch(/NFS/i);
  });

  it("documents metrics_distinct_time_count for staff Sample Count help", () => {
    expect(getDescriptionForVariable("metrics_distinct_time_count")).toMatch(/distinct sample timestamps/i);
  });

  it("returns a description for code-derived definitions", () => {
    expect(getDescriptionForVariable("utilization")).toMatch(/GPU utilization/i);
    expect(getDescriptionForVariable("read_bytes")).toMatch(/Bytes read/i);
  });

  it("merges monitor event metadata from variableMetadataMonitorEvents", () => {
    expect(getDescriptionForVariable("CAS_READS")).toMatch(/DRAM|memory controller/i);
    expect(getDescriptionForVariable("port_xmit_data")).toMatch(/InfiniBand|fabric/i);
  });

  it("falls back for unknown variables", () => {
    expect(getDescriptionForVariable("unknown_metric_xyz")).toMatch(/Telemetry variable/i);
  });

  it("documents job summary Bokeh subplot metric column names", () => {
    expect(getDescriptionForVariable("cpu")).toMatch(/CPU cores in use/i);
    expect(getDescriptionForVariable("nfs_iops")).toMatch(/NFS client read/i);
    expect(getDescriptionForVariable("lustre_read_mb_s")).toMatch(/Lustre client read/i);
    expect(getDescriptionForVariable("fabric_mb_per_gflops")).toMatch(/Fabric bandwidth/i);
  });

  it("returns null for metrics with no doc text (sf evictrate)", () => {
    expect(VARIABLE_METADATA.avg_sf_evictrate).toBeUndefined();
    expect(getDescriptionForVariable("avg_sf_evictrate")).toMatch(/Telemetry variable/i);
  });
});
