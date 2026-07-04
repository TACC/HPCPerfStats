import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/api-error";
import { getErrorMessage, getStatusAwareErrorMessage } from "@/api/get-error-message";

describe("get-error-message", () => {
  it("prefers ApiError message", () => {
    const err = new ApiError("Job not found", 404, { error: "Job not found" });
    expect(getErrorMessage(err, "fallback")).toBe("Job not found");
  });

  it("reads error key from plain object bodies", () => {
    expect(getErrorMessage({ error: "staff only" }, "fallback")).toBe("staff only");
  });

  it("prefers human detail over snake_case error key", () => {
    expect(
      getErrorMessage(
        {
          error: "histogram_job_count_exceeded",
          detail: "Too many jobs for histogram generation.",
        },
        "fallback",
      ),
    ).toBe("Too many jobs for histogram generation.");
  });

  it("returns status-aware copy for 403", () => {
    const err = new ApiError("Forbidden", 403, { detail: "Forbidden" });
    expect(getStatusAwareErrorMessage(err, "fallback")).toContain("permission");
  });
});
