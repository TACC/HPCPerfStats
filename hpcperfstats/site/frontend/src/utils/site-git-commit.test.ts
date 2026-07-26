import { describe, expect, it } from "vitest";
import {
  GITHUB_COMMIT_BASE,
  gitCommitHref,
  isDisplayableGitCommit,
  shortGitCommitLabel,
} from "./site-git-commit";

describe("site-git-commit", () => {
  it("accepts full and short hex SHAs", () => {
    expect(isDisplayableGitCommit("abcdef1")).toBe(true);
    expect(
      isDisplayableGitCommit("abcdef1234567890abcdef1234567890abcdef12"),
    ).toBe(true);
  });

  it("rejects empty, unknown, and non-hex", () => {
    expect(isDisplayableGitCommit("")).toBe(false);
    expect(isDisplayableGitCommit("unknown")).toBe(false);
    expect(isDisplayableGitCommit("not-a-sha!")).toBe(false);
    expect(isDisplayableGitCommit(null)).toBe(false);
  });

  it("builds short label and GitHub href", () => {
    const full = "Abcdef1234567890abcdef1234567890abcdef12";
    expect(shortGitCommitLabel(full)).toBe("abcdef1");
    expect(gitCommitHref(full)).toBe(
      `${GITHUB_COMMIT_BASE}abcdef1234567890abcdef1234567890abcdef12`,
    );
  });
});
