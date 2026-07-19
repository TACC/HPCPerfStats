"""Path → domain-rule mapping for Cursor hooks (keep in sync with agent-discipline-core.mdc)."""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, TypedDict

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired


class RouterEntry(TypedDict):
    id: str
    patterns: list[str]
    rules: list[str]
    exact_paths: NotRequired[list[str]]


# Mirror agent-discipline-core.mdc task router. Update both in the same task when triggers change.
HPCPERFSTATS_ROUTER_ENTRIES: list[RouterEntry] = [
    {
        "id": "sync_timedb_core",
        "patterns": [
            "hpcperfstats/dbload/sync_timedb.py",
            "hpcperfstats/dbload/sync_timedb_archive.py",
            "hpcperfstats/dbload/lib/**",
            "hpcperfstats/dbload/lib/sync_timedb_*.py",
            "hpcperfstats/dbload/lib/multiprocessing_pool_health.py",
        ],
        "rules": [
            "sync-timedb-persistence-contract.mdc",
            "sync-timedb-archive-janitor-contract.mdc",
            "sync-timedb-db-before-archive-contract.mdc",
            "sync-timedb-lock-and-archive-contract.mdc",
            "sync-timedb-post-chunk-archive-hygiene.mdc",
            "sync-timedb-change-regression-gate.mdc",
            "sync-timedb-hot-path-janitor-lock-priority.mdc",
            "runtime-resource-and-metrics-safety.mdc",
            "process-title-and-pool-labels.mdc",
            "bugfix-and-perf-change-playbook.mdc",
            "test-first-discipline.mdc",
        ],
    },
    {
        "id": "sync_timedb_regression_gate",
        "patterns": [
            "hpcperfstats/tests/test_sync_timedb*.py",
            "tests/run_sync_timedb_regression_battery.sh",
        ],
        "rules": [
            "sync-timedb-change-regression-gate.mdc",
            "test-first-discipline.mdc",
        ],
    },
    {
        "id": "sync_timedb_parsing_collapse",
        "patterns": [
            "hpcperfstats/dbload/lib/sync_timedb_parsing.py",
            "hpcperfstats/tests/test_sync_timedb.py",
        ],
        "rules": [
            "sync-timedb-parsing-collapse-vectorization.mdc",
        ],
    },
    {
        "id": "sync_timedb_startup",
        "patterns": [
            "hpcperfstats/dbload/lib/sync_timedb_startup_*.py",
        ],
        "rules": [
            "sync-timedb-canonical-startup-archive-scan.mdc",
        ],
    },
    {
        "id": "sync_timedb_ingest_pool",
        "patterns": [
            "hpcperfstats/dbload/lib/sync_timedb_archive_members_redis.py",
            "hpcperfstats/dbload/lib/sync_timedb_archive_helpers.py",
            "hpcperfstats/dbload/lib/sync_timedb_populate_pool.py",
        ],
        "rules": [
            "sync-timedb-ingest-pool-io-coordination.mdc",
            "sync-timedb-hot-path-janitor-lock-priority.mdc",
        ],
    },
    {
        "id": "metrics_batch",
        "patterns": [
            "hpcperfstats/analysis/metrics/*",
            "hpcperfstats/analysis/metrics/lib/**",
        ],
        "rules": [
            "runtime-resource-and-metrics-safety.mdc",
            "pipeline-metrics-e2e-maintenance.mdc",
            "update-metrics-job-listing-new-data-type.mdc",
            "update-metrics-batch-resilience.mdc",
        ],
    },
    {
        "id": "django_api",
        "patterns": [
            "hpcperfstats/site/lib/machine/api.py",
            "hpcperfstats/site/lib/machine/job_list_filter_summary.py",
            "hpcperfstats/site/lib/machine/serializers*",
            "hpcperfstats/site/lib/machine/models*",
        ],
        "rules": [
            "django-python-cursor-rules.mdc",
            "end-to-end-feature-wiring-contract.mdc",
            "design-focused-spa-ux.mdc",
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
            "hpcperfstats/site/lib/machine/migrations/*",
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
        "id": "frontend_prod_test_build",
        "patterns": [
            "hpcperfstats/site/frontend/package.json",
            "hpcperfstats/site/frontend/next.config.ts",
            "hpcperfstats/site/frontend/tsconfig*.json",
            "hpcperfstats/site/frontend/scripts/copy-next-export.mjs",
            "hpcperfstats/site/frontend/test/*",
            "hpcperfstats/site/frontend/app/bokeh-playwright-smoke/*",
            "scripts/rebuild_frontend.sh",
        ],
        "exact_paths": [
            "Dockerfile",
            ".dockerignore",
        ],
        "rules": [
            "frontend-prod-test-build-boundary.mdc",
            "dockerignore-test-artifacts-sync.mdc",
        ],
    },
    {
        "id": "frontend",
        "patterns": [
            "hpcperfstats/site/frontend/*",
        ],
        "rules": [
            "react-next-ts-cursor-rule.mdc",
            "shadcn-ui-frontend.mdc",
            "testing-best-practices.mdc",
            "frontend-stack-wiring-contract.mdc",
            "interactive-ready-controls.mdc",
            "design-focused-spa-ux.mdc",
        ],
    },
    {
        "id": "openapi_orval",
        "patterns": [
            "hpcperfstats/site/openapi/*",
            "hpcperfstats/site/lib/machine/openapi_schema.py",
            "hpcperfstats/site/lib/machine/openapi_serializers.py",
            "hpcperfstats/site/lib/machine/tests/test_*openapi_wire_contract*.py",
            "hpcperfstats/site/frontend/orval.config.ts",
            "hpcperfstats/site/frontend/src/api/*",
        ],
        "rules": [
            "openapi-orval-sync.mdc",
            "openapi-spa-wire-validation-contract.mdc",
            "frontend-stack-wiring-contract.mdc",
            "api-breaking-change-contract.mdc",
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
        "id": "docker_compose_app_example",
        "patterns": [
            "docker-compose.app.yaml",
            "docker-compose.app.yaml.example",
        ],
        "rules": [
            "docker-compose-app-example-sync.mdc",
            "readme-installation-sync.mdc",
        ],
    },
    {
        "id": "dockerignore_test_artifacts",
        "patterns": [
            ".dockerignore",
            "Dockerfile",
            "hpcperfstats/tests/test_dockerignore_test_artifacts.py",
        ],
        "rules": [
            "dockerignore-test-artifacts-sync.mdc",
        ],
    },
    {
        "id": "web_pages_e2e",
        "patterns": [
            "hpcperfstats/site/lib/machine/tests/test_web_pages*.py",
            "hpcperfstats/site/lib/machine/tests/test_bokeh_job_list_embed_browser_e2e.py",
        ],
        "rules": [
            "web-pages-e2e-test-maintenance.mdc",
            "web-pages-full-e2e-completion-gate.mdc",
        ],
    },
    {
        "id": "bokeh_version_upgrade",
        "patterns": [
            "hpcperfstats/site/frontend/package.json",
            "pyproject.toml",
            "hpcperfstats/site/lib/machine/tests/test_bokeh*.py",
        ],
        "rules": [
            "bokeh-version-and-vendor-patch-upgrade.mdc",
        ],
    },
    {
        "id": "frontend_a11y",
        "patterns": [
            "hpcperfstats/site/frontend/test/vitest/axe-test-utils.ts",
            "hpcperfstats/site/frontend/**/*.test.tsx",
            "tests/pipeline_e2e/test_a11y_axe_browser.py",
        ],
        "rules": [
            "frontend-a11y-regression.mdc",
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
            "compose-operator-terminal-commands.mdc",
        ],
    },
    {
        "id": "testing_docs",
        "patterns": [
            "docs/TESTING.md",
        ],
        "rules": [
            "testing-doc-sync.mdc",
            "compose-operator-terminal-commands.mdc",
        ],
    },
    {
        "id": "plans",
        "patterns": [
            "docs/plans/*",
            ".cursor/plans/*",
        ],
        "rules": [
            "plan-creation-contract.mdc",
            "plan-live-disk-sync.mdc",
            "plan-template-enforcement.mdc",
            "compose-operator-terminal-commands.mdc",
            "deploy-ini-with-code-no-phase-zero.mdc",
            "operator-command-lessons-learned.mdc",
        ],
    },
    {
        "id": "compose_operator_rule",
        "patterns": [
            "hpcperfstats/cursor-rules/compose-operator-terminal-commands.mdc",
        ],
        "rules": [
            "operator-command-lessons-learned.mdc",
        ],
    },
    {
        "id": "deploy_ini_redeploy",
        "patterns": [
            "hpcperfstats.ini.example",
            "docs/DEPLOY_CONCURRENCY_AND_NUMA.md",
        ],
        "rules": [
            "deploy-ini-with-code-no-phase-zero.mdc",
            "immutable-image-ini-policy.mdc",
            "hpcperfstats-ini-format.mdc",
        ],
    },
    {
        "id": "cursor_hooks",
        "patterns": [
            "cursor-hooks/*",
            "HPCPerfStats/cursor-hooks/*",
            ".cursor/hooks/*",
            "cursor-hooks/hooks.json",
            ".cursor/hooks.json",
            "hpcperfstats/tests/test_cursor_hooks.py",
        ],
        "rules": [
            "testing-best-practices.mdc",
        ],
    },
    {
        "id": "bokeh_plots",
        "patterns": [
            "hpcperfstats/site/lib/machine/bokeh_plot_layout.py",
            "hpcperfstats/site/frontend/**/*.jsx",
            "hpcperfstats/site/frontend/**/*.js",
            "hpcperfstats/analysis/metrics/lib/plot/**",
            "hpcperfstats/cursor-rules/bokeh-layout-surface-split.mdc",
        ],
        "rules": [
            "job-plot-artifacts-caching.mdc",
            "bokeh-layout-surface-split.mdc",
            "no-scientific-notation-web.mdc",
        ],
    },
    {
        "id": "package_lib_colocation",
        "patterns": [
            "hpcperfstats/**/lib/**",
            ".gitignore",
        ],
        "rules": [
            "package-lib-colocation.mdc",
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

# Backward-compatible alias for hpcperfstats-only callers/tests.
ROUTER_ENTRIES = HPCPERFSTATS_ROUTER_ENTRIES

# Mirror HPCPerfStats/monitor/cursor-rules/agent-discipline-core.mdc task router.
MONITOR_ROUTER_ENTRIES: list[RouterEntry] = [
    {
        "id": "monitor_c_src",
        "patterns": [
            "HPCPerfStats/monitor/src/**",
            "monitor/src/**",
        ],
        "rules": [
            "monitor-c-conventions.mdc",
            "monitor-jitter-and-fidelity-priority.mdc",
            "monitor-c-refactor-standards.mdc",
        ],
    },
    {
        "id": "monitor_ib",
        "patterns": [
            "HPCPerfStats/monitor/src/*ib*",
            "HPCPerfStats/monitor/src/opa.c",
            "HPCPerfStats/monitor/src/opa_sysfs.*",
            "monitor/src/*ib*",
            "monitor/src/opa.c",
            "monitor/src/opa_sysfs.*",
        ],
        "rules": [
            "monitor-ib-sysfs-parsing.mdc",
        ],
    },
    {
        "id": "monitor_gpu_dcgm",
        "patterns": [
            "HPCPerfStats/monitor/src/*dcgm*",
            "HPCPerfStats/monitor/src/*gpu*",
            "monitor/src/*dcgm*",
            "monitor/src/*gpu*",
        ],
        "rules": [
            "monitor-dcgm-integration.mdc",
        ],
    },
    {
        "id": "monitor_debug_shm",
        "patterns": [
            "HPCPerfStats/monitor/src/*debug*",
            "monitor/src/*debug*",
        ],
        "rules": [
            "monitor-debug-shm.mdc",
        ],
    },
    {
        "id": "monitor_shm_message_correctness",
        "patterns": [
            "HPCPerfStats/monitor/scripts/emit_build_capabilities.py",
            "HPCPerfStats/monitor/scripts/build_message_expectations.py",
            "HPCPerfStats/monitor/scripts/validate_shm_messages.py",
            "HPCPerfStats/monitor/scripts/lib/*",
            "HPCPerfStats/monitor/tests/test_shm_message_correctness.sh",
            "HPCPerfStats/monitor/tests/expected/shm_*",
            "HPCPerfStats/monitor/tests/expected/capabilities_*",
            "HPCPerfStats/monitor/tests/expected/expectations_*",
            "monitor/scripts/emit_build_capabilities.py",
            "monitor/scripts/build_message_expectations.py",
            "monitor/scripts/validate_shm_messages.py",
            "monitor/scripts/lib/*",
            "monitor/tests/test_shm_message_correctness.sh",
            "monitor/tests/expected/shm_*",
            "monitor/tests/expected/capabilities_*",
            "monitor/tests/expected/expectations_*",
        ],
        "rules": [
            "monitor-shm-message-correctness.mdc",
            "monitor-shm-validation-probe-parity.mdc",
        ],
    },
    {
        "id": "monitor_shm_probe_parity",
        "patterns": [
            "HPCPerfStats/monitor/src/net.c",
            "HPCPerfStats/monitor/src/mem.c",
            "HPCPerfStats/monitor/src/numa.c",
            "HPCPerfStats/monitor/src/block.c",
            "HPCPerfStats/monitor/src/ib.c",
            "HPCPerfStats/monitor/src/opa.c",
            "HPCPerfStats/monitor/src/cpu.c",
            "HPCPerfStats/monitor/src/intel_spr_imc.c",
            "HPCPerfStats/monitor/scripts/lib/host_live_probes.py",
            "HPCPerfStats/monitor/scripts/lib/device_validate.py",
            "HPCPerfStats/monitor/scripts/lib/live_spot_check.py",
            "HPCPerfStats/monitor/tests/test_shm_validation_lib.py",
            "monitor/src/net.c",
            "monitor/src/mem.c",
            "monitor/src/numa.c",
            "monitor/src/block.c",
            "monitor/src/ib.c",
            "monitor/src/opa.c",
            "monitor/src/cpu.c",
            "monitor/src/intel_spr_imc.c",
            "monitor/scripts/lib/host_live_probes.py",
            "monitor/scripts/lib/device_validate.py",
            "monitor/scripts/lib/live_spot_check.py",
            "monitor/tests/test_shm_validation_lib.py",
        ],
        "rules": [
            "monitor-shm-validation-probe-parity.mdc",
        ],
    },
    {
        "id": "monitor_emit_contract",
        "patterns": [
            "HPCPerfStats/monitor/src/stats*",
            "HPCPerfStats/monitor/src/rabbitmq*",
            "HPCPerfStats/monitor/src/**/KEYS*",
            "monitor/src/stats*",
            "monitor/src/rabbitmq*",
            "monitor/src/**/KEYS*",
        ],
        "rules": [
            "monitor-workspace-contract.mdc",
            "monitor-collect-tier-gating.mdc",
            "monitor-schema-keys-headers.mdc",
            "monitor-rabbitmq-integration-required.mdc",
            "monitor-emitted-variable-naming.mdc",
            "monitor-consumer-schema-migration.mdc",
            "monitor-consumer-side-plan.mdc",
        ],
    },
    {
        "id": "monitor_build",
        "patterns": [
            "HPCPerfStats/monitor/configure.ac",
            "HPCPerfStats/monitor/Makefile.am",
            "HPCPerfStats/monitor/src/Makefile.am",
            "HPCPerfStats/monitor/scripts/**",
            "monitor/configure.ac",
            "monitor/Makefile.am",
            "monitor/src/Makefile.am",
            "monitor/scripts/**",
            "configure.ac",
            "Makefile.am",
            "scripts/build_static_bundle.sh",
            "scripts/cross_compile_test.sh",
        ],
        "rules": [
            "monitor-static-build-verification.mdc",
            "monitor-dual-verify-cross-and-static.mdc",
            "monitor-post-verify-distclean.mdc",
            "monitor-build-clean-workspace.mdc",
            "configure-autoconf-awk-m4.mdc",
            "monitor-local-build-deps.mdc",
            "global-testing-discipline.mdc",
        ],
    },
    {
        "id": "monitor_tests",
        "patterns": [
            "HPCPerfStats/monitor/tests/**",
            "monitor/tests/**",
        ],
        "rules": [
            "monitor-c-testing-standards.mdc",
            "monitor-c-new-function-unittests.mdc",
            "global-testing-discipline.mdc",
            "traceback-fix-discipline.mdc",
        ],
    },
    {
        "id": "monitor_packaging",
        "patterns": [
            "HPCPerfStats/monitor/hpcperfstats.spec",
            "monitor/hpcperfstats.spec",
            "hpcperfstats.spec",
        ],
        "rules": [
            "monitor-version-and-packaging.mdc",
            "monitor-static-build-verification.mdc",
        ],
    },
    {
        "id": "monitor_readme",
        "patterns": [
            "HPCPerfStats/monitor/README.md",
            "HPCPerfStats/monitor/tests/README.md",
            "monitor/README.md",
            "monitor/tests/README.md",
        ],
        "rules": [
            "monitor-readme-maintenance.mdc",
        ],
    },
    {
        "id": "monitor_cursor_rules",
        "patterns": [
            "HPCPerfStats/monitor/cursor-rules/*.mdc",
            "monitor/cursor-rules/*.mdc",
        ],
        "rules": [
            "agent-discipline-core.mdc",
            "implementation-review-workflow.mdc",
            "plan-template-enforcement.mdc",
            "cursor-rules-maker.mdc",
            "monitor-cursor-rules-sync.mdc",
        ],
    },
    {
        "id": "monitor_cursor_workspace_sync",
        "patterns": [
            ".cursor/**",
            "HPCPerfStats/.cursor/**",
            ".cursor/rules/**",
            "cursor-hooks/**",
            "HPCPerfStats/cursor-hooks/**",
        ],
        "rules": [
            "monitor-cursor-rules-sync.mdc",
            "workspace-single-cursor-directory.mdc",
        ],
    },
    {
        "id": "monitor_plans",
        "patterns": [
            "HPCPerfStats/monitor/docs/plans/*",
            "monitor/docs/plans/*",
            ".cursor/plans/*",
        ],
        "rules": [
            "plan-creation-contract.mdc",
            "plan-live-disk-sync.mdc",
            "plan-template-enforcement.mdc",
        ],
    },
    {
        "id": "monitor_hooks",
        "patterns": [
            "cursor-hooks/*",
            "HPCPerfStats/cursor-hooks/*",
            ".cursor/hooks/*",
            "cursor-hooks/hooks.json",
            ".cursor/hooks.json",
            "hpcperfstats/tests/test_cursor_hooks.py",
        ],
        "rules": [
            "global-testing-discipline.mdc",
        ],
    },
    {
        "id": "authorized_hps",
        "patterns": [
            "HPCPerfStats/hpcperfstats/**",
            "hpcperfstats/**",
        ],
        "rules": [
            "out-of-monitor-hpcperfstats-rules.mdc",
        ],
    },
    {
        "id": "monitor_bugfix",
        "patterns": [
            "HPCPerfStats/monitor/src/**",
            "HPCPerfStats/monitor/tests/**",
            "monitor/src/**",
            "monitor/tests/**",
        ],
        "rules": [
            "logic-change-checklist.mdc",
            "every-error-regression-test.mdc",
        ],
    },
]


def normalize_repo_path(path: str) -> str:
    normalized = (path or "").replace("\\", "/").lstrip("/")
    anchors = (
        "hpcperfstats/",
        "monitor/",
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


def _rules_from_entries(paths: Iterable[str], entries: list[RouterEntry]) -> list[str]:
    seen: dict[str, str] = {}
    for raw in paths:
        normalized = normalize_repo_path(raw)
        if not normalized:
            continue
        for entry in entries:
            if not entry_matches_path(normalized, entry):
                continue
            for rule in entry["rules"]:
                key = rule.lower()
                if key not in seen:
                    seen[key] = rule
    return list(seen.values())


def detect_rules_profile(workspace_roots: Iterable[str] | None = None) -> str:
    """Return 'monitor' or 'hpcperfstats' from .cursor/rules symlink or on-disk layout."""
    roots = list(workspace_roots or [])
    hook_dir = Path(__file__).resolve().parent
    checkout_root = hook_dir.parent
    candidates: list[Path] = []
    for root in roots:
        candidates.append(Path(root) / ".cursor" / "rules")
    candidates.append(checkout_root.parent / ".cursor" / "rules")

    for rules_path in candidates:
        if not rules_path.exists():
            continue
        try:
            target = str(rules_path.resolve()).replace("\\", "/")
        except OSError:
            target = str(rules_path).replace("\\", "/")
        if "monitor/cursor-rules" in target:
            return "monitor"
        if "hpcperfstats/cursor-rules" in target:
            return "hpcperfstats"

    monitor_core = checkout_root / "monitor" / "cursor-rules" / "agent-discipline-core.mdc"
    hps_core = checkout_root / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc"
    if monitor_core.is_file() and not hps_core.is_file():
        return "monitor"
    if hps_core.is_file():
        return "hpcperfstats"
    return "monitor"


def profile_rules_dir_label(profile: str) -> str:
    if profile == "monitor":
        return "HPCPerfStats/monitor/cursor-rules"
    return "hpcperfstats/cursor-rules"


def router_entries_for_profile(profile: str) -> list[RouterEntry]:
    if profile == "monitor":
        return MONITOR_ROUTER_ENTRIES
    return HPCPERFSTATS_ROUTER_ENTRIES


def triggered_rules_for_paths(
    paths: Iterable[str],
    *,
    workspace_roots: Iterable[str] | None = None,
) -> list[str]:
    """Merge monitor + hpcperfstats router hits (cross-edit turns may match both)."""
    seen: dict[str, str] = {}
    for rule in _rules_from_entries(paths, MONITOR_ROUTER_ENTRIES):
        seen[rule.lower()] = rule
    for rule in _rules_from_entries(paths, HPCPERFSTATS_ROUTER_ENTRIES):
        seen[rule.lower()] = rule
    return list(seen.values())
