import { describe, expect, it } from "vitest";
import {
  buildProcTable,
  meanNumericTexts,
} from "./proc-table";

const fmt = (n: number) => String(n);

describe("proc-table", () => {
  it("skips blank strings when averaging (not Number(\"\") → 0)", () => {
    expect(meanNumericTexts(["", "4", ""], fmt)).toBe("4");
    expect(meanNumericTexts(["", ""], fmt)).toBe("");
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
