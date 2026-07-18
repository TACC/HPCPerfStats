import { describe, expect, it } from "vitest";
import { buildJobsRetrieveParams } from "./jobs-retrieve-params";

describe("buildJobsRetrieveParams", () => {
  it("keeps calendar browse end_time__date (day) for GET /api/jobs/", () => {
    const out = buildJobsRetrieveParams({
      end_time__date: "2024-01-15",
      page: "1",
    });
    expect(out.end_time__date).toBe("2024-01-15");
    expect(out.page).toBe(1);
  });

  it("keeps month and year end_time__date browse values", () => {
    expect(buildJobsRetrieveParams({ end_time__date: "2024-01" }).end_time__date).toBe(
      "2024-01",
    );
    expect(buildJobsRetrieveParams({ end_time__date: "2024" }).end_time__date).toBe("2024");
  });

  it("keeps expanded end_time__date__gte / __lte when present", () => {
    const out = buildJobsRetrieveParams({
      end_time__date__gte: "2024-01-01",
      end_time__date__lte: "2024-01-31",
    });
    expect(out.end_time__date__gte).toBe("2024-01-01");
    expect(out.end_time__date__lte).toBe("2024-01-31");
  });

  it("still allowlists Extended Search end_time__gte / __lte", () => {
    const out = buildJobsRetrieveParams({
      end_time__gte: "2024-01-01T00:00:00",
      end_time__lte: "2024-01-31T23:59:59",
    });
    expect(out.end_time__gte).toBe("2024-01-01T00:00:00");
    expect(out.end_time__lte).toBe("2024-01-31T23:59:59");
  });

  it("strips unknown keys", () => {
    const out = buildJobsRetrieveParams({
      end_time__date: "2024-01-15",
      evil: "drop-me",
    });
    expect(out.end_time__date).toBe("2024-01-15");
    expect(out).not.toHaveProperty("evil");
  });
});
