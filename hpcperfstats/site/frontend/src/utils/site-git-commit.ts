/** GitHub commit URLs for the staff actions menu (baked SITE_GIT_COMMIT). */

export const GITHUB_COMMIT_BASE = "https://github.com/TACC/HPCPerfStats/commit/";

export function isDisplayableGitCommit(value: string | undefined | null): boolean {
  const v = (value ?? "").trim();
  return /^[0-9a-f]{7,40}$/i.test(v);
}

export function shortGitCommitLabel(fullSha: string): string {
  return fullSha.trim().slice(0, 7).toLowerCase();
}

export function gitCommitHref(fullSha: string): string {
  return `${GITHUB_COMMIT_BASE}${fullSha.trim().toLowerCase()}`;
}
