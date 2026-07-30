/** GitHub tree URLs for the staff actions menu (baked SITE_GIT_COMMIT). */

export const GITHUB_TREE_BASE = "https://github.com/TACC/HPCPerfStats/tree/";

export function isDisplayableGitCommit(value: string | undefined | null): boolean {
  const v = (value ?? "").trim();
  return /^[0-9a-f]{7,40}$/i.test(v);
}

export function shortGitCommitLabel(fullSha: string): string {
  return fullSha.trim().slice(0, 7).toLowerCase();
}

export function gitCommitMenuLabel(fullSha: string): string {
  return `Current github commit: ${shortGitCommitLabel(fullSha)}`;
}

export function gitCommitHref(fullSha: string): string {
  return `${GITHUB_TREE_BASE}${fullSha.trim().toLowerCase()}`;
}
