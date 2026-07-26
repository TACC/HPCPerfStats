import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTypeDetailQuery } from "./use-type-detail";

vi.mock("@/api/generated/jobs/jobs", () => ({
  useJobsRetrieve3: vi.fn(),
}));

import { useJobsRetrieve3 } from "@/api/generated/jobs/jobs";

describe("useTypeDetailQuery", () => {
  beforeEach(() => {
    vi.mocked(useJobsRetrieve3).mockReset();
  });

  it("loads type detail when jid and type are set", () => {
    vi.mocked(useJobsRetrieve3).mockReturnValue({
      data: { type_name: "cpu", jobid: "123" },
      error: null,
      isLoading: false,
    } as ReturnType<typeof useJobsRetrieve3>);

    const { result } = renderHook(() => useTypeDetailQuery("123", "cpu"));

    expect(useJobsRetrieve3).toHaveBeenCalledWith(
      "123",
      "cpu",
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: true,
          select: expect.any(Function),
          placeholderData: expect.any(Function),
        }),
      }),
    );
    expect(result.current.data?.type_name).toBe("cpu");
  });

  it("does not fetch without route params", () => {
    vi.mocked(useJobsRetrieve3).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
    } as ReturnType<typeof useJobsRetrieve3>);

    renderHook(() => useTypeDetailQuery("", "cpu"));

    expect(useJobsRetrieve3).toHaveBeenCalledWith(
      "",
      "cpu",
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: false,
          select: expect.any(Function),
          placeholderData: expect.any(Function),
        }),
      }),
    );
  });
});
