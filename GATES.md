# Gates: gcc-16-dockerfiles

OWNS: services-conf/gcc-alpine.Dockerfile, services-conf/assert_gcc_min_version.sh, services-conf/db.Dockerfile, services-conf/proxy.Dockerfile, Dockerfile, scripts/rebuild_full_site.sh, scripts/test_rebuild_full_site.sh, hpcperfstats/tests/test_dockerfile_gcc_toolchain.py, hpcperfstats/tests/test_dockerfile_db_postgres.py, hpcperfstats/tests/test_docker_compose_healthchecks.py, hpcperfstats/tests/test_dockerfile_jemalloc_cpython.py, hpcperfstats/tests/test_dockerfile_python_deps_cache.py, hpcperfstats/cursor-rules/python-image-interpreter-contract.mdc, hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc, README.md, docs/TESTING.md, docs/upgrade.md

Scope: GCC ≥ 16.2 on compile-bearing Dockerfiles, musl toolchain image, rebuild_full_site build phase, contract tests, and operator docs.

- [x] G1: Dockerfile GCC contract tests pass
  CHECK: /data/HPCPerfStats/.venv/bin/python3 -m pytest hpcperfstats/tests/test_dockerfile_gcc_toolchain.py hpcperfstats/tests/test_dockerfile_db_postgres.py::test_db_dockerfile_uses_shared_musl_gcc_toolchain hpcperfstats/tests/test_docker_compose_healthchecks.py::test_proxy_dockerfile_uses_shared_musl_gcc_toolchain hpcperfstats/tests/test_dockerfile_jemalloc_cpython.py::test_python_build_uses_gcc_16_from_testing_pin hpcperfstats/tests/test_dockerfile_python_deps_cache.py::test_python_build_pins_gcc_16_toolchain -q
  EXPECT: passed
  CWD: HPCPerfStats
  EVIDENCE: test_runs/gcc-16-contract-pytest.log exit 0 2026-10-05 (39 tests in extended db/jemalloc/deps run)

- [x] G2: rebuild_full_site static contract passes
  CHECK: bash ./scripts/test_rebuild_full_site.sh
  EXPECT: all checks passed
  CWD: HPCPerfStats
  EVIDENCE: test_runs/gcc-16-test-rebuild-full-site.log exit 0 2026-10-05

- [ ] G3: Full-site build-only (musl GCC + compose images)
  CHECK: ./scripts/rebuild_full_site.sh --build-only
  EXPECT: Skipping up -d
  CWD: HPCPerfStats
  EVIDENCE: pending
