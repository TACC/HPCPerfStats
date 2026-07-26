import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { normalizeGitCommit, resolveGitCommit } from "./site-git-commit.mjs";

describe("normalizeGitCommit", () => {
  it("keeps hex SHAs and lowercases", () => {
    assert.equal(normalizeGitCommit("ABCDEF1"), "abcdef1");
    assert.equal(
      normalizeGitCommit("abcdef1234567890abcdef1234567890abcdef12"),
      "abcdef1234567890abcdef1234567890abcdef12",
    );
  });

  it("rejects empty, unknown, and garbage", () => {
    assert.equal(normalizeGitCommit(""), "");
    assert.equal(normalizeGitCommit("unknown"), "");
    assert.equal(normalizeGitCommit("UNKNOWN"), "");
    assert.equal(normalizeGitCommit("not-a-sha"), "");
  });
});

describe("resolveGitCommit", () => {
  it("prefers HPCPERFSTATS_GIT_COMMIT when set", () => {
    const sha = "deadbeef1234567890abcdef1234567890abcdef";
    assert.equal(
      resolveGitCommit({
        env: { HPCPERFSTATS_GIT_COMMIT: sha },
        gitCheckoutRoot: "/tmp",
        execFileSyncImpl: () => {
          throw new Error("should not call git");
        },
      }),
      sha,
    );
  });

  it("ignores unknown env and falls back to git", () => {
    const sha = "abcdef1234567890abcdef1234567890abcdef12";
    assert.equal(
      resolveGitCommit({
        env: { HPCPERFSTATS_GIT_COMMIT: "unknown" },
        gitCheckoutRoot: "/repo",
        execFileSyncImpl: () => `${sha}\n`,
      }),
      sha,
    );
  });

  it("returns empty when env unset and git fails", () => {
    assert.equal(
      resolveGitCommit({
        env: {},
        gitCheckoutRoot: "/repo",
        execFileSyncImpl: () => {
          throw new Error("no git");
        },
      }),
      "",
    );
  });
});
