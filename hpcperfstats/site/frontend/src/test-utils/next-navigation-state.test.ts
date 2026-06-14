import { describe, expect, it } from "vitest";
import {
  configureNextNavigationFromPath,
  nextNavigationMock,
} from "./next-navigation-state";

describe("configureNextNavigationFromPath", () => {
  it("sets catch-all slug and flat date param for calendar routes", () => {
    configureNextNavigationFromPath("/machine/date/2024-01-15/");
    expect(nextNavigationMock.pathname).toBe("/machine/date/2024-01-15/");
    expect(nextNavigationMock.params.slug).toEqual(["date", "2024-01-15"]);
    expect(nextNavigationMock.params.date).toBe("2024-01-15");
  });

  it("sets slug and year param for year browse routes", () => {
    configureNextNavigationFromPath("/machine/year/2022/");
    expect(nextNavigationMock.params.slug).toEqual(["year", "2022"]);
    expect(nextNavigationMock.params.year).toBe("2022");
  });

  it("updates params when router.push navigates client-side", () => {
    configureNextNavigationFromPath("/machine/");
    nextNavigationMock.router.push("/machine/date/2024-06-01/");
    expect(nextNavigationMock.params.slug).toEqual(["date", "2024-06-01"]);
    expect(nextNavigationMock.params.date).toBe("2024-06-01");
  });
});
