import { execFileSync } from "node:child_process";

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
 * Resolve commit: HPCPERFSTATS_GIT_COMMIT env (Docker build-arg), else host git.
 * @param {{
 *   env?: NodeJS.ProcessEnv,
 *   gitCheckoutRoot: string,
 *   execFileSyncImpl?: typeof execFileSync,
 * }} opts
 * @returns {string}
 */
export function resolveGitCommit({
  env = process.env,
  gitCheckoutRoot,
  execFileSyncImpl = execFileSync,
}) {
  const fromEnv = normalizeGitCommit(env.HPCPERFSTATS_GIT_COMMIT);
  if (fromEnv) return fromEnv;
  try {
    const sha = execFileSyncImpl("git", ["-C", gitCheckoutRoot, "rev-parse", "HEAD"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return normalizeGitCommit(sha);
  } catch {
    return "";
  }
}
