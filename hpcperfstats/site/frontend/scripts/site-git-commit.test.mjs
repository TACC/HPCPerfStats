import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  normalizeGitCommit,
  resolveGitCommit,
  resolveGitCommitFromGitDir,
} from "./site-git-commit.mjs";

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

describe("resolveGitCommitFromGitDir", () => {
  const sha = "abcdef1234567890abcdef1234567890abcdef12";
  const root = "/repo";

  it("reads detached HEAD", () => {
    const files = {
      "/repo/.git": { isFile: () => false, isDirectory: () => true },
      "/repo/.git/HEAD": `${sha}\n`,
    };
    assert.equal(
      resolveGitCommitFromGitDir(root, {
        existsSync: (p) => p in files || p === "/repo/.git",
        statSync: (p) => files[p],
        readFileSync: (p) => files[p],
      }),
      sha,
    );
  });

  it("reads ref file for branch HEAD (Docker bake regression)", () => {
    // ENV=unknown + silent git rev-parse failure must still resolve via .git files.
    const files = {
      "/repo/.git": { isFile: () => false, isDirectory: () => true },
      "/repo/.git/HEAD": "ref: refs/heads/main\n",
      "/repo/.git/refs/heads/main": `${sha}\n`,
    };
    assert.equal(
      resolveGitCommitFromGitDir(root, {
        existsSync: (p) => p in files,
        statSync: (p) => files[p],
        readFileSync: (p) => {
          if (typeof files[p] === "string") return files[p];
          throw new Error(`unexpected read ${p}`);
        },
      }),
      sha,
    );
  });

  it("reads packed-refs when loose ref is missing", () => {
    const files = {
      "/repo/.git": { isFile: () => false, isDirectory: () => true },
      "/repo/.git/HEAD": "ref: refs/heads/main\n",
      "/repo/.git/packed-refs": `# pack-refs\n${sha} refs/heads/main\n`,
    };
    assert.equal(
      resolveGitCommitFromGitDir(root, {
        existsSync: (p) => p in files,
        statSync: (p) => files[p],
        readFileSync: (p) => {
          if (p.endsWith("refs/heads/main")) {
            const err = new Error("ENOENT");
            err.code = "ENOENT";
            throw err;
          }
          if (typeof files[p] === "string") return files[p];
          throw new Error(`unexpected read ${p}`);
        },
      }),
      sha,
    );
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
        gitDirIo: {
          existsSync: () => false,
        },
      }),
      sha,
    );
  });

  it("ignores unknown env and prefers .git files over git binary", () => {
    const sha = "abcdef1234567890abcdef1234567890abcdef12";
    const files = {
      "/repo/.git": { isFile: () => false, isDirectory: () => true },
      "/repo/.git/HEAD": "ref: refs/heads/main\n",
      "/repo/.git/refs/heads/main": `${sha}\n`,
    };
    assert.equal(
      resolveGitCommit({
        env: { HPCPERFSTATS_GIT_COMMIT: "unknown" },
        gitCheckoutRoot: "/repo",
        execFileSyncImpl: () => {
          throw new Error("git should not be required");
        },
        gitDirIo: {
          existsSync: (p) => p in files,
          statSync: (p) => files[p],
          readFileSync: (p) => files[p],
        },
      }),
      sha,
    );
  });

  it("falls back to git rev-parse when .git files unavailable", () => {
    const sha = "abcdef1234567890abcdef1234567890abcdef12";
    assert.equal(
      resolveGitCommit({
        env: { HPCPERFSTATS_GIT_COMMIT: "unknown" },
        gitCheckoutRoot: "/repo",
        execFileSyncImpl: () => `${sha}\n`,
        gitDirIo: { existsSync: () => false },
      }),
      sha,
    );
  });

  it("returns empty when env unset and all backends fail", () => {
    assert.equal(
      resolveGitCommit({
        env: {},
        gitCheckoutRoot: "/repo",
        execFileSyncImpl: () => {
          throw new Error("no git");
        },
        gitDirIo: { existsSync: () => false },
      }),
      "",
    );
  });
});
