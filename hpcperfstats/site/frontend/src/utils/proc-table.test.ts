import { describe, expect, it } from "vitest";
import {
  buildProcTable,
  meanNumericTexts,
  PROC_TABLE_COLUMNS,
} from "./proc-table";

const fmt = (n: number) =>
  new Intl.NumberFormat("en-US", {
    notation: "standard",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);

describe("proc-table", () => {
  it("skips blank strings when averaging (not Number(\"\") → 0)", () => {
    expect(meanNumericTexts(["", "4", ""], fmt)).toBe("4.00");
    expect(meanNumericTexts(["", ""], fmt)).toBe("");
  });

  it("labels memory columns in MB and converts API kB values", () => {
    for (const key of ["vm_peak", "vm_hwm", "vm_stk", "vm_exe", "vm_lib"] as const) {
      const col = PROC_TABLE_COLUMNS.find((c) => c.key === key);
      expect(col?.label).toMatch(/\(MB\)$/);
      expect(col?.label).not.toMatch(/kB/i);
    }

    const table = buildProcTable(
      [
        {
          host: "h1",
          proc: "app",
          uid: 1,
          vm_peak: 8192,
          vm_hwm: 4096,
          vm_stk: 128,
          vm_exe: 256,
          vm_lib: 512,
          threads: 2,
        },
        {
          host: "h2",
          proc: "app",
          uid: 1,
          vm_peak: 4096,
          vm_hwm: 2048,
          vm_stk: 64,
          vm_exe: 128,
          vm_lib: 256,
          threads: 4,
        },
      ],
      fmt,
    );
    expect(table.legacyOnly).toBe(false);
    expect(table.columns.map((c) => c.label)).toEqual([
      "Process",
      "Host",
      "Peak VM (MB)",
      "HWM (MB)",
      "Stack (MB)",
      "Text (MB)",
      "Libs (MB)",
      "Threads",
    ]);
    const group = table.groups[0];
    expect(group.rows[0].vm_peak).toBe("8.00");
    expect(group.rows[0].vm_hwm).toBe("4.00");
    expect(group.rows[0].vm_stk).toBe("0.13");
    expect(group.rows[0].vm_exe).toBe("0.25");
    expect(group.rows[0].vm_lib).toBe("0.50");
    expect(group.rows[0].threads).toBe("2");
    expect(group.averages.vm_peak).toBe("6.00");
    expect(group.averages.vm_hwm).toBe("3.00");
    expect(group.averages.threads).toBe("3.00");
  });

  it("builds columns for peak/hwm/stk/exe/lib/threads", () => {
    const table = buildProcTable(
      [
        {
          host: "h1",
          proc: "app",
          uid: 1,
          vm_peak: 100,
          vm_hwm: 50,
          vm_stk: 10,
          vm_exe: 20,
          vm_lib: 30,
          threads: 2,
        },
      ],
      fmt,
    );
    expect(table.legacyOnly).toBe(false);
    const keys = table.columns.map((c) => c.key);
    expect(keys).toEqual([
      "proc",
      "host",
      "vm_peak",
      "vm_hwm",
      "vm_stk",
      "vm_exe",
      "vm_lib",
      "threads",
    ]);
    expect(keys).not.toContain("uid");
    expect(keys).not.toContain("vm_rss");
    expect(keys).not.toContain("vm_size");
  });
});
