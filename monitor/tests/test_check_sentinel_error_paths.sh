#!/bin/sh
# scripts/check_sentinel_error_paths.py: clean on src/; flags stats_set(..., 0) and a silent
# continue/return/goto (optionally after logging) after a failed read; accepts sentinel paths and
# allowlisted cases; rejects stale or reasonless allowlist entries.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${PY}"; then
  PY="$(command -v python3)"
fi
LINT="${ROOT}/scripts/check_sentinel_error_paths.py"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

"${PY}" -I "${LINT}" "${ROOT}/src"

expect_findings() {
  # $1 = expected finding count, $2 = file, $3... = extra lint args
  want="$1"
  file="$2"
  shift 2
  if "${PY}" -I "${LINT}" "$@" "${file}" >"${WORK}/out.txt"; then
    rc=0
  else
    rc=1
  fi
  got="$(grep -c ': if (' "${WORK}/out.txt" || true)"
  if test "${got}" != "${want}" || { test "${want}" = 0 && test "${rc}" != 0; }; then
    echo "FAIL: ${file}: want ${want} finding(s), got ${got} (rc=${rc})" >&2
    cat "${WORK}/out.txt" >&2
    exit 1
  fi
}

cat >"${WORK}/zero.c" <<'C'
void f(struct stats *s, int fd)
{
  if (pread(fd, b, n, 0) < 0) {
    stats_set(s, "k", 0);
    return;
  }
  stats_set(s, "k", v);
}
C
expect_findings 1 "${WORK}/zero.c"

cat >"${WORK}/bare.c" <<'C'
void g(struct stats *s, const char *path)
{
  FILE *fp;

  for (i = 0; i < n; i++) {
    fp = fopen(path, "r");
    if (fp == NULL)
      continue;
    stats_inc(s, "k", 1);
  }
  if (pscanf(path, "%llu", &v) != 1)
    return;
  if (statfs(path, &sfs) < 0) {
    ERROR("cannot stat %s\n", path);
    goto out;
  }
out:
  free(buf);
}
C
expect_findings 3 "${WORK}/bare.c"

cat >"${WORK}/ok.c" <<'C'
void h(struct stats *s, const char *path)
{
  FILE *fp = NULL;

  if (pscanf(path, "%llu", &v) != 1) {
    stats_set_missing(s, "k", STATS_SENTINEL_READ_FAILED);
    return;
  }
  fp = fopen(path, "r");
  if (fp == NULL)
    return -1;
  stats_set(s, "k", v);
  /* if (read(fd, b, n) < 0) return; is only a comment */
}
C
expect_findings 0 "${WORK}/ok.c"

# Declaration with an initializer: `FILE *fp = fopen(...); if (fp == NULL) return;`.
printf '%s\n' 'static void f(struct stats *stats) { FILE *fp = fopen("/x", "r"); if (fp == NULL) return; stats_set(stats, "k", 1); }' \
  >"${WORK}/decl.c"
expect_findings 1 "${WORK}/decl.c"
printf '%s\n' 'static void g(struct stats *stats) { int same = n == 1; if (same == 0) return; stats_set(stats, "k", 1); }' \
  >"${WORK}/cmp.c"
expect_findings 0 "${WORK}/cmp.c"

# A file that stores no stats is not a collector.
printf '%s\n' 'void i(void) { if (read(fd, b, n) < 0) return; }' >"${WORK}/notcollector.c"
expect_findings 0 "${WORK}/notcollector.c"

printf '%s\n' 'bare.c: fp == NULL # test: discovery only' 'bare.c: pscanf(path, "%llu", &v) != 1 # test' \
  'bare.c: statfs(path, &sfs) < 0 # test' \
  >"${WORK}/allow.txt"
expect_findings 0 "${WORK}/bare.c" --allowlist "${WORK}/allow.txt"

printf '%s\n' 'ok.c: gone() < 0 # test: stale' >"${WORK}/stale.txt"
if "${PY}" -I "${LINT}" --allowlist "${WORK}/stale.txt" "${WORK}/ok.c" >"${WORK}/out.txt"; then
  echo "FAIL: stale allowlist entry accepted" >&2
  exit 1
fi
grep -q 'stale entry ok.c' "${WORK}/out.txt"

printf '%s\n' 'ok.c: x < 0' >"${WORK}/noreason.txt"
if "${PY}" -I "${LINT}" --allowlist "${WORK}/noreason.txt" "${WORK}/ok.c" >/dev/null 2>&1; then
  echo "FAIL: allowlist entry without a reason accepted" >&2
  exit 1
fi
echo "test_check_sentinel_error_paths passed"
