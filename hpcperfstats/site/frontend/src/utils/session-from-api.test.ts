import { describe, expect, it } from "vitest";
import { sessionFromApi } from "./session-from-api";

describe("sessionFromApi", () => {
  it("forwards separate_test_login from the session API payload", () => {
    expect(
      sessionFromApi({
        logged_in: true,
        username: "alice",
        is_staff: true,
        machine_name: "cluster.test",
        separate_test_login: true,
      }).separate_test_login,
    ).toBe(true);
  });

  it("treats a missing separate_test_login flag as false", () => {
    expect(
      sessionFromApi({
        logged_in: true,
        username: "alice",
        is_staff: true,
      }).separate_test_login,
    ).toBe(false);
  });

  it("falls back to the site machine name when the payload omits it", () => {
    const session = sessionFromApi({
      logged_in: true,
      username: "alice",
      is_staff: false,
    });
    expect(session.machine_name).toBeTruthy();
    expect(session.separate_test_login).toBe(false);
  });
});
