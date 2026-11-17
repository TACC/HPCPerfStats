#!/usr/bin/env python3
"""Check scripts/lib/fault_ci_files.py against tests/Makefile.am and src/.

1. CI_COVERED_FILES equals the src files with a stats_set_missing* call site that the
   test_fault_inject_* targets link.
2. Every such target enforces coverage: it links test_site_coverage.c or its driver checks
   stats_sentinel_sites_report.
3. Every src file with a call site is in CI_COVERED_FILES (no failure site outside CI).
Exit 1 with one line per problem, 0 when consistent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib.fault_ci_files import CI_COVERED_FILES  # noqa: E402

_CALL = re.compile(r"\b(stats_set_missing|stats_set_missing_all|stats_type_set_missing_all)\s*\(")


def has_call_site(path: Path) -> bool:
    """True when the C file calls a stats_set_missing* setter (comments and the `(name)(...)`
    definitions in stats.c do not count)."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)
    return any(not re.match(r"\s*(void|enum)\b", line) and _CALL.search(line) for line in text.splitlines())


def makefile_sources(makefile: Path) -> dict[str, list[str]]:
    """target -> source tokens for every *_SOURCES assignment, `$(x_SOURCES)` references expanded."""
    joined = makefile.read_text(encoding="utf-8").replace("\\\n", " ")
    raw: dict[str, list[str]] = {}
    for m in re.finditer(r"^(\w+)_SOURCES\s*\+?=\s*(.*)$", joined, re.M):
        raw.setdefault(m.group(1), []).extend(m.group(2).split())

    def expand(target: str, seen: frozenset[str] = frozenset()) -> list[str]:
        out: list[str] = []
        for tok in raw.get(target, []):
            ref = re.fullmatch(r"\$\((\w+)_SOURCES\)", tok)
            if ref and ref.group(1) not in seen:
                out.extend(expand(ref.group(1), seen | {target}))
            else:
                out.append(tok)
        return out

    return {t: expand(t) for t in raw}


def main() -> int:
    problems: list[str] = []
    sources = makefile_sources(MONITOR / "tests" / "Makefile.am")
    fault_targets = {t: s for t, s in sources.items() if t.startswith("test_fault_inject_")}
    linked: set[str] = set()
    for target, toks in sorted(fault_targets.items()):
        driver = MONITOR / "tests" / f"{target}.c"
        enforced = "test_site_coverage.c" in toks or (
            driver.is_file() and "stats_sentinel_sites_report(" in driver.read_text(encoding="utf-8")
        )
        if not enforced:
            problems.append(f"{target}: links neither test_site_coverage.c nor checks stats_sentinel_sites_report")
        for tok in toks:
            if tok.startswith("$(top_srcdir)/src/") and tok.endswith(".c"):
                path = MONITOR / "src" / tok.rsplit("/", 1)[1]
                if path.is_file() and has_call_site(path):
                    linked.add(path.name)
    for name in sorted(linked - CI_COVERED_FILES):
        problems.append(f"{name}: linked into a fault target but missing from CI_COVERED_FILES")
    for name in sorted(CI_COVERED_FILES - linked):
        problems.append(f"{name}: in CI_COVERED_FILES but no fault target links it")
    with_sites = {p.name for p in (MONITOR / "src").glob("*.c") if has_call_site(p)}
    for name in sorted(with_sites - CI_COVERED_FILES):
        problems.append(f"{name}: has failure sites but no fault-coverage target")
    for line in problems:
        print(line)
    print(f"check_fault_ci_files: {'FAIL' if problems else 'OK'} ({len(CI_COVERED_FILES)} files, {len(fault_targets)} targets)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
