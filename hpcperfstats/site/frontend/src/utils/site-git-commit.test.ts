import { describe, expect, it } from "vitest";
import {
  GITHUB_TREE_BASE,
  gitCommitHref,
  gitCommitMenuLabel,
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

  it("builds short label, menu label, and GitHub tree href", () => {
    const full = "Abcdef1234567890abcdef1234567890abcdef12";
    expect(shortGitCommitLabel(full)).toBe("abcdef1");
    expect(gitCommitMenuLabel(full)).toBe("Current github commit: abcdef1");
    expect(gitCommitHref(full)).toBe(
      `${GITHUB_TREE_BASE}abcdef1234567890abcdef1234567890abcdef12`,
    );
    expect(gitCommitHref(full)).toContain("/tree/");
    expect(gitCommitHref(full)).not.toContain("/commit/");
  });
});
