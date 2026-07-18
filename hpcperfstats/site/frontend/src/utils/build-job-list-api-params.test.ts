import { describe, expect, it } from "vitest";
import {
  buildJobListApiParams,
  buildJobListHistogramApiParams,
} from "./build-job-list-api-params";

describe("buildJobListApiParams", () => {
  it("merges route filters over query entries", () => {
    const sp = new URLSearchParams("page=2&foo=bar");
    const params = buildJobListApiParams(sp, {
      year: "",
      date: "2024-05-01",
      username: "alice",
      account: undefined,
      queue: "batch",
      host: "n1.example.com",
    });
    expect(params.page).toBe("2");
    expect(params.foo).toBe("bar");
    expect(params.end_time__date).toBe("2024-05-01");
    expect(params.username).toBe("alice");
    expect(params.queue).toBe("batch");
    expect(params.host).toBe("n1.example.com");
    expect(params.account).toBeUndefined();
  });

  it("applies date after year when both route segments are set", () => {
    const sp = new URLSearchParams("");
    const params = buildJobListApiParams(sp, {
      year: "2023",
      date: "2024-06-01",
    });
    expect(params.end_time__date).toBe("2024-06-01");
  });

  it("passes through query-only params when route is empty", () => {
    const sp = new URLSearchParams("queue=debug&page=3");
    const params = buildJobListApiParams(sp, {});
    expect(params.queue).toBe("debug");
    expect(params.page).toBe("3");
  });
});

describe("buildJobListHistogramApiParams", () => {
  it("strips page and order_by while keeping filter identity", () => {
    const sp = new URLSearchParams(
      "page=2&order_by=-runtime&queue=normal&end_time__date=2024-01-15&view=charts",
    );
    const params = buildJobListHistogramApiParams(sp, { username: "alice" });
    expect(params).toEqual({
      queue: "normal",
      end_time__date: "2024-01-15",
      username: "alice",
    });
    expect(params.page).toBeUndefined();
    expect(params.order_by).toBeUndefined();
    expect(params.view).toBeUndefined();
  });
});
