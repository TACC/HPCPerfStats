import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

/**
 * Normalize a candidate git SHA for SITE_GIT_COMMIT.
 * Rejects empty, "unknown", and non-hex strings.
 * @param {string | undefined | null} raw
 * @returns {string} full SHA or ""
 */
export function normalizeGitCommit(raw) {
  const value = (raw ?? "").trim();
  if (!value || value.toLowerCase() === "unknown") return "";
  if (!/^[0-9a-f]{7,40}$/i.test(value)) return "";
  return value.toLowerCase();
}

/**
 * Resolve SHA by reading `.git/HEAD` (+ ref / packed-refs). Works in Docker
 * when `git rev-parse` fails (safe.directory, missing git, incomplete PATH).
 * @param {string} gitCheckoutRoot
 * @param {{
 *   existsSync?: typeof fs.existsSync,
 *   readFileSync?: typeof fs.readFileSync,
 *   statSync?: typeof fs.statSync,
 * }} [io]
 * @returns {string}
 */
export function resolveGitCommitFromGitDir(gitCheckoutRoot, io = {}) {
  const existsSync = io.existsSync ?? fs.existsSync;
  const readFileSync = io.readFileSync ?? fs.readFileSync;
  const statSync = io.statSync ?? fs.statSync;

  let gitDir = path.join(gitCheckoutRoot, ".git");
  try {
    if (!existsSync(gitDir)) return "";
    const st = statSync(gitDir);
    if (st.isFile()) {
      const text = readFileSync(gitDir, "utf8").trim();
      const m = text.match(/^gitdir:\s*(.+)$/i);
      if (!m) return "";
      const pointed = m[1].trim();
      gitDir = path.isAbsolute(pointed)
        ? pointed
        : path.resolve(gitCheckoutRoot, pointed);
      if (!existsSync(gitDir)) return "";
    } else if (!st.isDirectory()) {
      return "";
    }
  } catch {
    return "";
  }

  let head;
  try {
    head = readFileSync(path.join(gitDir, "HEAD"), "utf8").trim();
  } catch {
    return "";
  }

  const direct = normalizeGitCommit(head);
  if (direct) return direct;

  const refMatch = head.match(/^ref:\s*(.+)$/i);
  if (!refMatch) return "";
  const refName = refMatch[1].trim();

  try {
    const sha = readFileSync(path.join(gitDir, refName), "utf8").trim();
    const fromRef = normalizeGitCommit(sha);
    if (fromRef) return fromRef;
  } catch {
    /* packed-refs fallback below */
  }

  try {
    const packed = readFileSync(path.join(gitDir, "packed-refs"), "utf8");
    for (const line of packed.split(/\r?\n/)) {
      if (!line || line.startsWith("#") || line.startsWith("^")) continue;
      const parts = line.trim().split(/\s+/);
      if (parts.length < 2) continue;
      const [sha, name] = parts;
      if (name === refName) {
        const fromPacked = normalizeGitCommit(sha);
        if (fromPacked) return fromPacked;
      }
    }
  } catch {
    return "";
  }
  return "";
}

/**
 * Resolve commit: HPCPERFSTATS_GIT_COMMIT env (Docker build-arg), else `.git`
 * files, else `git rev-parse`.
 * @param {{
 *   env?: NodeJS.ProcessEnv,
 *   gitCheckoutRoot: string,
 *   execFileSyncImpl?: typeof execFileSync,
 *   gitDirIo?: Parameters<typeof resolveGitCommitFromGitDir>[1],
 * }} opts
 * @returns {string}
 */
export function resolveGitCommit({
  env = process.env,
  gitCheckoutRoot,
  execFileSyncImpl = execFileSync,
  gitDirIo,
}) {
  const fromEnv = normalizeGitCommit(env.HPCPERFSTATS_GIT_COMMIT);
  if (fromEnv) return fromEnv;

  const fromFiles = resolveGitCommitFromGitDir(gitCheckoutRoot, gitDirIo);
  if (fromFiles) return fromFiles;

  try {
    const sha = execFileSyncImpl("git", ["-C", gitCheckoutRoot, "rev-parse", "HEAD"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    return normalizeGitCommit(sha);
  } catch {
    return "";
  }
}
