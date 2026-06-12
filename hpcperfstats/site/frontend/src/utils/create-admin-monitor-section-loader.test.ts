import { describe, expect, it, vi } from "vitest";
import { createAdminMonitorSectionLoader } from "./create-admin-monitor-section-loader";

describe("createAdminMonitorSectionLoader", () => {
  it("loads section, picks response, clears loading", async () => {
    const setLoading = vi.fn();
    const setError = vi.fn();
    const setData = vi.fn();
    const getAdminMonitorSection = vi.fn().mockResolvedValue({ foo: [1] });
    const load = createAdminMonitorSectionLoader({
      section: "hosts",
      pickResponse: (res) => res.foo,
      setLoading,
      setError,
      setData,
      apiClient: { getAdminMonitorSection },
    });
    const done = load(false);
    expect(setLoading).toHaveBeenCalledWith(true);
    expect(setError).toHaveBeenCalledWith(null);
    await done;
    expect(getAdminMonitorSection).toHaveBeenCalledWith("hosts", { refresh: false });
    expect(setData).toHaveBeenCalledWith([1]);
    expect(setLoading).toHaveBeenLastCalledWith(false);
  });

  it("records error message on rejection", async () => {
    const setLoading = vi.fn();
    const setError = vi.fn();
    const setData = vi.fn();
    const getAdminMonitorSection = vi.fn().mockRejectedValue(new Error("net"));
    const load = createAdminMonitorSectionLoader({
      section: "cache",
      pickResponse: () => null,
      setLoading,
      setError,
      setData,
      apiClient: { getAdminMonitorSection },
    });
    await load(true);
    expect(getAdminMonitorSection).toHaveBeenCalledWith("cache", { refresh: true });
    expect(setError).toHaveBeenCalledWith("net");
    expect(setLoading).toHaveBeenLastCalledWith(false);
  });
});
