"""Path → domain-rule mapping for Cursor hooks (keep in sync with agent-discipline-core.mdc)."""
from __future__ import annotations

import fnmatch
from typing import Iterable, NotRequired, TypedDict


class RouterEntry(TypedDict):
    id: str
    patterns: list[str]
    rules: list[str]
    exact_paths: NotRequired[list[str]]


# Mirror agent-discipline-core.mdc task router. Update both in the same task when triggers change.
ROUTER_ENTRIES: list[RouterEntry] = [
    {
        "id": "sync_timedb_core",
        "patterns": [
            "hpcperfstats/dbload/sync_timedb*.py",
            "hpcperfstats/dbload/sync_timedb_*.py",
            "hpcperfstats/dbload/multiprocessing_pool_health.py",
        ],
        "rules": [
            "sync-timedb-persistence-contract.mdc",
            "sync-timedb-archive-janitor-contract.mdc",
            "sync-timedb-db-before-archive-contract.mdc",
            "sync-timedb-lock-and-archive-contract.mdc",
            "runtime-resource-and-metrics-safety.mdc",
        ],
    },
    {
        "id": "sync_timedb_startup",
        "patterns": [
            "hpcperfstats/dbload/sync_timedb_startup_*.py",
        ],
        "rules": [
            "sync-timedb-startup-day-close-contract.mdc",
            "sync-timedb-startup-tar-seal-contract.mdc",
            "sync-timedb-canonical-startup-archive-scan.mdc",
        ],
    },
    {
        "id": "sync_timedb_ingest_pool",
        "patterns": [
            "hpcperfstats/dbload/sync_timedb_archive_members_redis.py",
            "hpcperfstats/dbload/sync_timedb_archive_helpers.py",
        ],
        "rules": [
            "sync-timedb-ingest-pool-io-coordination.mdc",
        ],
    },
    {
        "id": "metrics_batch",
        "patterns": [
            "hpcperfstats/analysis/metrics/*",
        ],
        "rules": [
            "runtime-resource-and-metrics-safety.mdc",
            "pipeline-metrics-e2e-maintenance.mdc",
            "update-metrics-job-listing-new-data-type.mdc",
        ],
    },
    {
        "id": "django_api",
        "patterns": [
            "hpcperfstats/site/machine/api.py",
            "hpcperfstats/site/machine/serializers*",
            "hpcperfstats/site/machine/models*",
        ],
        "rules": [
            "django-python-cursor-rules.mdc",
            "end-to-end-feature-wiring-contract.mdc",
        ],
    },
    {
        "id": "api_routes",
        "patterns": [
            "hpcperfstats/site/hpcperfstats_site/urls.py",
            "hpcperfstats/site/hpcperfstats_site/api_urls.py",
            "hpcperfstats/tests/urlconf_route_catalog.py",
            "hpcperfstats/tests/test_endpoint_route_snapshot.py",
            "tests/pipeline_e2e/test_all_endpoints_browser.py",
        ],
        "rules": [
            "api-breaking-change-contract.mdc",
            "pipeline-endpoint-matrix-drift-guard.mdc",
        ],
    },
    {
        "id": "migrations",
        "patterns": [
            "hpcperfstats/site/machine/migrations/*",
            "hpcperfstats/site/*/migrations/*",
        ],
        "rules": [
            "django-migration-numbering.mdc",
        ],
    },
    {
        "id": "ini_compose_config",
        "patterns": [
            "hpcperfstats.ini*",
            "hpcperfstats/hpcperfstats.ini*",
            "docker-compose*.yaml",
        ],
        "rules": [
            "immutable-image-ini-policy.mdc",
            "hpcperfstats-ini-format.mdc",
        ],
    },
    {
        "id": "frontend",
        "patterns": [
            "hpcperfstats/site/frontend/*",
        ],
        "rules": [
            "react-js-cursor-rule.mdc",
            "testing-best-practices.mdc",
        ],
    },
    {
        "id": "compose_workflows",
        "patterns": [
            "docker-compose*.yaml",
            "tests/run_*_workflow.sh",
        ],
        "rules": [
            "colima-docker-runtime.mdc",
            "compose-required-for-data-services-changes.mdc",
            "docker-compose-non-unit-testing.mdc",
        ],
    },
    {
        "id": "nginx",
        "patterns": [
            "services-conf/nginx*",
        ],
        "rules": [
            "nginx-static-url-prefix.mdc",
            "nginx-django-route-allowlist-sync.mdc",
        ],
    },
    {
        "id": "readme_install",
        "patterns": [
            "HPCPerfStats/README.md",
        ],
        "exact_paths": [
            "README.md",
        ],
        "rules": [
            "readme-installation-sync.mdc",
        ],
    },
    {
        "id": "testing_docs",
        "patterns": [
            "docs/TESTING.md",
        ],
        "rules": [
            "testing-doc-sync.mdc",
        ],
    },
    {
        "id": "plans",
        "patterns": [
            "docs/plans/*",
        ],
        "rules": [
            "plan-creation-contract.mdc",
            "plan-template-enforcement.mdc",
        ],
    },
    {
        "id": "cursor_hooks",
        "patterns": [
            ".cursor/hooks/*",
            ".cursor/hooks.json",
            "hpcperfstats/tests/test_cursor_hooks.py",
        ],
        "rules": [
            "testing-best-practices.mdc",
        ],
    },
    {
        "id": "cursor_rules",
        "patterns": [
            "hpcperfstats/cursor-rules/*.mdc",
        ],
        "rules": [
            "agent-discipline-core.mdc",
            "implementation-review-workflow.mdc",
            "plan-template-enforcement.mdc",
        ],
    },
]


def normalize_repo_path(path: str) -> str:
    normalized = (path or "").replace("\\", "/").lstrip("/")
    anchors = (
        "hpcperfstats/",
        "services-conf/",
        "tests/",
        "docs/",
        ".cursor/",
        "docker-compose",
        "HPCPerfStats/",
    )
    for anchor in anchors:
        idx = normalized.find(anchor)
        if idx >= 0:
            return normalized[idx:]
    return normalized


def is_workspace_root_readme(normalized_path: str) -> bool:
    """Workspace/git-root operator README only — not hooks, cursor-rules, or package READMEs."""
    if normalized_path == "README.md":
        return True
    if not normalized_path.endswith("/README.md"):
        return False
    blocked = (".cursor/", "hpcperfstats/", "HPCPerfStats/")
    return not any(marker in normalized_path for marker in blocked)


def entry_matches_path(normalized_path: str, entry: RouterEntry) -> bool:
    if any(path_matches_pattern(normalized_path, pattern) for pattern in entry["patterns"]):
        return True
    if normalized_path in (entry.get("exact_paths") or []):
        return True
    if entry["id"] == "readme_install" and is_workspace_root_readme(normalized_path):
        return True
    return False


def path_matches_pattern(normalized_path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(normalized_path, pattern):
        return True
    # Basename fallback only for simple filename patterns (never for `*` alone).
    if "/" not in pattern:
        tail = pattern.split("/")[-1]
        if tail and tail != "*":
            return fnmatch.fnmatch(normalized_path.split("/")[-1], tail)
    return False


def triggered_rules_for_paths(paths: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for raw in paths:
        normalized = normalize_repo_path(raw)
        if not normalized:
            continue
        for entry in ROUTER_ENTRIES:
            if not entry_matches_path(normalized, entry):
                continue
            for rule in entry["rules"]:
                key = rule.lower()
                if key not in seen:
                    seen[key] = rule
    return list(seen.values())
