# Gates: pipeline-memory-high-rebuild

OWNS: scripts/lib/compose_pipeline_memory_high.sh, scripts/rebuild_pipeline.sh, scripts/rebuild_full_site.sh, scripts/test_rebuild_pipeline.sh, scripts/test_rebuild_full_site.sh, docs/upgrade.md, docs/TESTING.md, hpcperfstats/cursor-rules/compose-operator-terminal-commands.mdc

Scope: Shared pipeline cgroup memory.high helper, rebuild script hooks, full-site rebuild script, static tests, and operator docs per live plan.

- [x] G1: Static rebuild pipeline tests pass
  CHECK: bash ./scripts/test_rebuild_pipeline.sh
  EXPECT: all checks passed
  CWD: HPCPerfStats
  EVIDENCE: test_runs/pipeline-memory-high-rebuild-static-*.log exit 0

- [x] G2: Static rebuild full-site tests pass
  CHECK: bash ./scripts/test_rebuild_full_site.sh
  EXPECT: all checks passed
  CWD: HPCPerfStats
  EVIDENCE: test_rebuild_full_site.sh exit 0 2026-10-05
