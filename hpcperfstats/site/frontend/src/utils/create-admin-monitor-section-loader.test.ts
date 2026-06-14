import { describe, expect, it, vi } from "vitest";
import { createAdminMonitorSectionLoader } from "./create-admin-monitor-section-loader";

vi.mock("@/api/generated/admin/admin", () => ({
  adminMonitorRetrieve: vi.fn(),
}));

import { adminMonitorRetrieve } from "@/api/generated/admin/admin";

describe("createAdminMonitorSectionLoader", () => {
  it("loads section, picks response, clears loading", async () => {
    const setLoading = vi.fn();
    const setError = vi.fn();
    const setData = vi.fn();
    vi.mocked(adminMonitorRetrieve).mockResolvedValue({ foo: [1] });
    const load = createAdminMonitorSectionLoader({
      section: "hosts",
      pickResponse: (res) => res.foo,
      setLoading,
      setError,
      setData,
    });
    const done = load(false);
    expect(setLoading).toHaveBeenCalledWith(true);
    expect(setError).toHaveBeenCalledWith(null);
    await done;
    expect(adminMonitorRetrieve).toHaveBeenCalledWith({
      section: "hosts",
      refresh: undefined,
    });
    expect(setData).toHaveBeenCalledWith([1]);
    expect(setLoading).toHaveBeenLastCalledWith(false);
  });

  it("records error message on rejection", async () => {
    const setLoading = vi.fn();
    const setError = vi.fn();
    const setData = vi.fn();
    vi.mocked(adminMonitorRetrieve).mockRejectedValue(new Error("net"));
    const load = createAdminMonitorSectionLoader({
      section: "cache",
      pickResponse: () => null,
      setLoading,
      setError,
      setData,
    });
    await load(true);
    expect(adminMonitorRetrieve).toHaveBeenCalledWith({
      section: "cache",
      refresh: "1",
    });
    expect(setError).toHaveBeenCalledWith("net");
    expect(setLoading).toHaveBeenLastCalledWith(false);
  });
});
