import { describe, expect, it } from "vitest";
import { formatRabbitmqHostBucketDownload } from "./format-rabbitmq-host-bucket-download";

describe("formatRabbitmqHostBucketDownload", () => {
  it("emits five bucket keys with comma-delimited hosts and empty lists", () => {
    const text = formatRabbitmqHostBucketDownload([
      { host: "b.example.com", last_time: "2026-09-14T00:00:00Z", age_bucket: "ok" },
      { host: "a.example.com", last_time: "2026-09-14T00:00:00Z", age_bucket: "ok" },
      { host: "late.example.com", last_time: null, age_bucket: "gt_week" },
      { host: "hour.example.com", last_time: "2026-09-14T00:00:00Z", age_bucket: "gt_hour" },
    ]);
    expect(text).toBe(
      [
        "10_m_or_less = [a.example.com,b.example.com]",
        "10_m_more = []",
        "1_h_more = [hour.example.com]",
        "1_d_more = []",
        "1_w_more = [late.example.com]",
        "",
      ].join("\n"),
    );
  });

  it("treats missing age_bucket as gt_week", () => {
    const text = formatRabbitmqHostBucketDownload([
      { host: "silent.example.com", last_time: null },
    ]);
    expect(text).toContain("1_w_more = [silent.example.com]");
  });
});
