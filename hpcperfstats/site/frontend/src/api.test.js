import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api from "./api";

describe("api client", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    document.cookie = "";
    delete window.location;
    window.location = {
      href: "",
      pathname: "/machine/jobs",
      search: "",
      assign: vi.fn(),
      replace: vi.fn(),
    };
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("getSession sends credentials and returns JSON on success", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ logged_in: true }),
    });
    const data = await api.getSession();
    expect(data).toEqual({ logged_in: true });
    expect(fetch).toHaveBeenCalledWith(
      "/api/session/",
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({
          Accept: "application/json",
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("includes X-CSRFToken when csrftoken cookie is set", async () => {
    document.cookie = "csrftoken=abc123; path=/";
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({}),
    });
    await api.getSession();
    const headers = fetch.mock.calls[0][1].headers;
    expect(headers["X-CSRFToken"] || headers.get?.("X-CSRFToken")).toBe("abc123");
  });

  it("redirects to login_prompt on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 401,
      ok: false,
      json: async () => ({}),
    });
    await expect(api.getHomeOptions()).rejects.toThrow("Unauthorized");
    expect(window.location.href).toContain("/login_prompt");
    expect(window.location.href).toContain("next=");
  });

  it("throws with detail message on error response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 400,
      ok: false,
      json: async () => ({ detail: "bad request" }),
    });
    await expect(api.getSession()).rejects.toThrow("bad request");
  });

  it("getJobPlots builds query string for progressive and zoom", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ plots: [] }),
    });
    await api.getJobPlots("42", "summary", true, true);
    const url = fetch.mock.calls[0][0];
    expect(url).toContain("/api/jobs/42/plots/");
    expect(url).toContain("plot=summary");
    expect(url).toContain("zoom=1");
    expect(url).toContain("progressive=1");
  });

  it("job histogram requests include group and embed version", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ plots: [] }),
    });
    await api.getJobQueueHistograms({ queue: "normal" });
    const queueUrl = fetch.mock.calls[0][0];
    expect(queueUrl).toContain("/api/jobs/histograms/");
    expect(queueUrl).toContain("group=queue");
    expect(queueUrl).toContain("_histogram_embed_v=4");

    await api.getJobMetricHistogram({ queue: "normal" }, "runtime");
    const metricUrl = fetch.mock.calls[1][0];
    expect(metricUrl).toContain("group=metric");
    expect(metricUrl).toContain("metric=runtime");
    expect(metricUrl).toContain("_histogram_embed_v=4");
  });
});
