/**
 * Job Detail Processes tab table builders (host_proc / proc_list).
 */

export type ProcListObject = {
  host?: string | number | null;
  proc?: string | number | null;
  device?: string | number | null;
  uid?: string | number | null;
  vm_peak?: string | number | null;
  vm_hwm?: string | number | null;
  vm_stk?: string | number | null;
  vm_exe?: string | number | null;
  vm_lib?: string | number | null;
  threads?: string | number | null;
};

export type ProcListEntry = string | ProcListObject;

/** Monitor/API host_proc memory fields are in kB (`U=kB`); UI shows MB. */
export const PROC_MEMORY_KB_KEYS = [
  "vm_peak",
  "vm_hwm",
  "vm_stk",
  "vm_exe",
  "vm_lib",
] as const;

const PROC_MEMORY_KB_KEY_SET = new Set<string>(PROC_MEMORY_KB_KEYS);

export const PROC_TABLE_COLUMNS: ReadonlyArray<{
  key: keyof ProcListObject;
  label: string;
}> = [
  { key: "proc", label: "Process" },
  { key: "host", label: "Host" },
  { key: "vm_peak", label: "Peak VM (MB)" },
  { key: "vm_hwm", label: "HWM (MB)" },
  { key: "vm_stk", label: "Stack (MB)" },
  { key: "vm_exe", label: "Text (MB)" },
  { key: "vm_lib", label: "Libs (MB)" },
  { key: "threads", label: "Threads" },
];

export const PROC_AVG_KEYS = [
  "vm_peak",
  "vm_hwm",
  "vm_stk",
  "vm_exe",
  "vm_lib",
  "threads",
] as const;

export function cellText(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  return String(value);
}

/** Convert monitor kB to display MB (1024 kB = 1 MB). */
export function kbToMbDisplay(
  value: string | number | null | undefined,
  formatDecimal: (n: number) => string,
): string {
  const text = cellText(value);
  if (text === "") return "";
  const n = Number(text);
  if (!Number.isFinite(n)) return text;
  return formatDecimal(n / 1024);
}

/** Mean of numeric cell texts; blank strings are skipped (not treated as 0). */
export function meanNumericTexts(
  values: string[],
  formatDecimal: (n: number) => string,
): string {
  const nums = values
    .filter((v) => v !== "")
    .map((v) => Number(v))
    .filter((n) => Number.isFinite(n));
  if (nums.length === 0) return "";
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  return formatDecimal(mean);
}

export type ProcTableGroup = {
  proc: string;
  hostCount: number;
  averages: Record<string, string>;
  rows: Array<Record<string, string>>;
};

export function buildProcTable(
  procList: ProcListEntry[],
  formatDecimal: (n: number) => string,
): {
  columns: Array<{ key: string; label: string }>;
  groups: ProcTableGroup[];
  /** Flat rows for legacy string-only lists. */
  rows: Array<Record<string, string>>;
  legacyOnly: boolean;
} {
  const legacyOnly = procList.every((entry) => typeof entry === "string");
  if (legacyOnly) {
    return {
      columns: [{ key: "proc", label: "Process" }],
      groups: [],
      rows: procList.map((entry) => ({ proc: String(entry) })),
      legacyOnly: true,
    };
  }

  const rows = procList.map((entry) => {
    if (typeof entry === "string") {
      return { proc: entry };
    }
    const row: Record<string, string> = {};
    for (const col of PROC_TABLE_COLUMNS) {
      const raw = entry[col.key];
      const text = PROC_MEMORY_KB_KEY_SET.has(col.key)
        ? kbToMbDisplay(raw, formatDecimal)
        : cellText(raw);
      if (text) row[col.key] = text;
    }
    if (!row.proc && entry.device != null && String(entry.device) !== "") {
      row.proc = String(entry.device);
    }
    return row;
  });

  const columns = PROC_TABLE_COLUMNS.filter((col) =>
    rows.some((row) => row[col.key] != null && row[col.key] !== ""),
  ).map((col) => ({ key: col.key, label: col.label }));

  if (columns.length === 0) {
    return {
      columns: [{ key: "proc", label: "Process" }],
      groups: [],
      rows: rows.map((row) => ({ proc: row.proc || "" })),
      legacyOnly: true,
    };
  }

  const byProc = new Map<string, Array<Record<string, string>>>();
  for (const row of rows) {
    const name = row.proc || "(unnamed)";
    const list = byProc.get(name) || [];
    list.push(row);
    byProc.set(name, list);
  }
  const groups: ProcTableGroup[] = Array.from(byProc.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([proc, groupRows]) => {
      const averages: Record<string, string> = {};
      for (const key of PROC_AVG_KEYS) {
        if (!columns.some((c) => c.key === key)) continue;
        averages[key] = meanNumericTexts(
          groupRows.map((r) => r[key] || ""),
          formatDecimal,
        );
      }
      return {
        proc,
        hostCount: groupRows.length,
        averages,
        rows: groupRows,
      };
    });

  return { columns, groups, rows: [], legacyOnly: false };
}
