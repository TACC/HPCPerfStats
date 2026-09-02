import { describe, expect, it } from "vitest";
import {
  parseMachinePathname,
  parseMachineSlug,
  resolveMachineSlugFromNavigation,
} from "./machine-route-params";

describe("parseMachineSlug", () => {
  it("maps home to search view", () => {
    expect(parseMachineSlug([])).toEqual({
      slug: [],
      flatParams: {},
      view: "search",
    });
  });

  it("maps date segment to job list flat params", () => {
    expect(parseMachineSlug(["date", "2024-01-15"])).toEqual({
      slug: ["date", "2024-01-15"],
      flatParams: { date: "2024-01-15" },
      view: "jobList",
    });
  });

  it("maps year segment to job list flat params", () => {
    expect(parseMachineSlug(["year", "2024"])).toEqual({
      slug: ["year", "2024"],
      flatParams: { year: "2024" },
      view: "jobList",
    });
  });

  it("maps job id to job detail", () => {
    expect(parseMachineSlug(["job", "991"])).toEqual({
      slug: ["job", "991"],
      flatParams: { pk: "991" },
      view: "jobDetail",
    });
  });

  it("maps job type detail route", () => {
    expect(parseMachineSlug(["job", "991", "cpu"])).toEqual({
      slug: ["job", "991", "cpu"],
      flatParams: { jid: "991", typeName: "cpu" },
      view: "typeDetail",
    });
  });

  it("maps host plot route", () => {
    expect(parseMachineSlug(["host", "n001", "plot"])).toEqual({
      slug: ["host", "n001", "plot"],
      flatParams: { host: "n001" },
      view: "hostDetail",
    });
  });

  it("maps test-login create page", () => {
    expect(parseMachineSlug(["test-login"])).toEqual({
      slug: ["test-login"],
      flatParams: {},
      view: "pageTestLogin",
    });
  });

  it("maps list routes by segment key", () => {
    expect(parseMachineSlug(["username", "alice"]).flatParams).toEqual({ username: "alice" });
    expect(parseMachineSlug(["account", "proj"]).flatParams).toEqual({ account: "proj" });
    expect(parseMachineSlug(["queue", "normal"]).flatParams).toEqual({ queue: "normal" });
    expect(parseMachineSlug(["host", "n001"]).flatParams).toEqual({ host: "n001" });
  });
});

describe("parseMachinePathname", () => {
  it("strips /machine prefix and trailing slash", () => {
    expect(parseMachinePathname("/machine/date/2024-01-15/")).toEqual([
      "date",
      "2024-01-15",
    ]);
    expect(parseMachinePathname("/machine/")).toEqual([]);
  });
});

describe("resolveMachineSlugFromNavigation", () => {
  it("prefers catch-all slug params over pathname", () => {
    expect(
      resolveMachineSlugFromNavigation(
        { slug: ["date", "2024-01-15"] },
        "/machine/",
      ),
    ).toEqual(["date", "2024-01-15"]);
  });

  it("falls back to pathname when slug param is absent", () => {
    expect(
      resolveMachineSlugFromNavigation({}, "/machine/year/2022/"),
    ).toEqual(["year", "2022"]);
  });
});
