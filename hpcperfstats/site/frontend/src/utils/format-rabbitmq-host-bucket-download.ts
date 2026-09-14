import type { AdminMonitorHostRow, FreshnessBucket } from "@/types/view-models";

export const RABBITMQ_HOST_BUCKET_DOWNLOAD_FILENAME = "rabbitmq_host_buckets.txt";

const DOWNLOAD_KEYS: { bucket: FreshnessBucket; key: string }[] = [
  { bucket: "ok", key: "10_m_or_less" },
  { bucket: "gt_10min", key: "10_m_more" },
  { bucket: "gt_hour", key: "1_h_more" },
  { bucket: "gt_day", key: "1_d_more" },
  { bucket: "gt_week", key: "1_w_more" },
];

export function formatRabbitmqHostBucketDownload(
  rows: AdminMonitorHostRow[],
): string {
  const grouped = new Map<FreshnessBucket, string[]>();
  for (const { bucket } of DOWNLOAD_KEYS) {
    grouped.set(bucket, []);
  }
  for (const row of rows) {
    const host = String(row.host || "").trim();
    if (!host) continue;
    const bucket = (row.age_bucket as FreshnessBucket) || "gt_week";
    const list = grouped.get(bucket);
    if (!list) continue;
    list.push(host);
  }
  return (
    DOWNLOAD_KEYS.map(({ bucket, key }) => {
      const hosts = [...(grouped.get(bucket) ?? [])].sort((a, b) =>
        a.localeCompare(b),
      );
      return `${key} = [${hosts.join(",")}]`;
    }).join("\n") + "\n"
  );
}
