import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/test-utils/legacy-api-facade";
import { ApiError } from "@/api/api-error";
import { fetchPubClusterDashboard } from "@/api/fetch-mutator";

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
      json: async () => ({
        logged_in: true,
        username: "alice",
        is_staff: false,
        machine_name: "test-cluster",
      }),
    });
    const data = await api.getSession();
    expect(data).toEqual({
      logged_in: true,
      username: "alice",
      is_staff: false,
      machine_name: "test-cluster",
    });
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
      json: async () => ({
        logged_in: false,
        username: "",
        is_staff: false,
        machine_name: "test-cluster",
      }),
    });
    await api.getSession();
    const headers = fetch.mock.calls[0][1].headers;
    expect(headers["X-CSRFToken"] || headers.get?.("X-CSRFToken")).toBe("abc123");
  });

  it("throws when mutating without csrftoken cookie", async () => {
    document.cookie = "csrftoken=; Max-Age=0; path=/";
    global.fetch = vi.fn();
    await expect(api.rotateUserApiKey()).rejects.toThrow("CSRF token missing");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("throws when session response fails schema validation", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ logged_in: "not-a-boolean" }),
    });
    await expect(api.getSession()).rejects.toThrow("API response validation failed");
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

  it("throws ApiError with status and body.error on 403", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 403,
      ok: false,
      json: async () => ({ error: "Forbidden action" }),
    });
    await expect(api.getSession()).rejects.toMatchObject({
      message: "Forbidden action",
      status: 403,
      body: { error: "Forbidden action" },
    });
    await expect(api.getSession()).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError with status 500", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 500,
      ok: false,
      json: async () => ({ detail: "Server exploded" }),
    });
    await expect(api.getHomeOptions()).rejects.toMatchObject({
      status: 500,
      message: "Server exploded",
    });
  });

  it("fetchPubClusterDashboard validates response shape", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ machine_name: false }),
    });
    await expect(fetchPubClusterDashboard()).rejects.toThrow(
      "API response validation failed",
    );
  });

  it("fetchPubClusterDashboard returns parsed bundle on success", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        machine_name: "test-cluster",
        expansion_factors: {},
      }),
    });
    const bundle = await fetchPubClusterDashboard();
    expect(bundle).toEqual({
      machine_name: "test-cluster",
      expansion_factors: {},
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/pub/cluster-dashboard/",
      expect.objectContaining({ credentials: "omit" }),
    );
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
    await api.getJobMetricHistogram({ queue: "normal" }, "runtime");
    const metricUrl = fetch.mock.calls[0][0];
    expect(metricUrl).toContain("group=metric");
    expect(metricUrl).toContain("metric=runtime");
    expect(metricUrl).toContain("_histogram_embed_v=9");
  });
});
