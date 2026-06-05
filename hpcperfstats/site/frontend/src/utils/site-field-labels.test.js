import { describe, expect, it } from "vitest";
import { JOB_LIST_TABLE_HEADERS, PROJECT_FIELD_LABEL } from "./site-field-labels";

describe("site-field-labels", () => {
  it("uses Project as the canonical project label", () => {
    expect(PROJECT_FIELD_LABEL).toBe("Project");
    expect(JOB_LIST_TABLE_HEADERS.project).toBe("Project");
  });

  it("exposes stable job list table header labels", () => {
    expect(JOB_LIST_TABLE_HEADERS).toEqual({
      jid: "Job ID",
      user: "User",
      project: "Project",
      queue: "Queue",
      nodes: "Nodes",
      nodeHrs: "Node hours",
      jobCount: "Jobs",
      performanceData: "Performance data",
    });
  });
});
