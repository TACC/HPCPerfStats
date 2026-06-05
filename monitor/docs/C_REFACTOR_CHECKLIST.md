# Monitor C refactor checklist (per file)

Use this checklist for each `.c`/`.h` pass under `HPCPerfStats/monitor/src/`. One file (or tight `.c`/`.h` pair) per PR.

## Before editing

- [ ] Identify phase queue entry and paired header.
- [ ] Read applicable rows in `HPCPerfStats/docs/monitor_variable_rename_map.yaml`.
- [ ] Note `#ifdef` / optional-hardware gates for this module.

## Audit

- [ ] List functions **>50 lines** to split.
- [ ] Grep file for `gets`, `strcpy`, `sprintf`, unsafe `strncpy`.
- [ ] List magic numbers to promote to `#define`/`enum`.
- [ ] Remove unused variables, unreachable code, stale comments.

## Refactor

- [ ] Apply YAML renames for this collector’s `st_name` and event keys.
- [ ] Replace unsafe string I/O; add NULL/bounds checks on pointer params.
- [ ] Add `const` where parameters are read-only.
- [ ] Split long functions; prefer linkable units if reused elsewhere.
- [ ] Unify style in **entire file**: 2-space indent, K&R braces, snake_case.
- [ ] File banner + brief docs on non-obvious helpers only.

## Tests

- [ ] Extend existing `tests/test_*.c` or add driver in `tests/Makefile.am`.
- [ ] Register new linkable helpers per **monitor-c-new-function-unittests**.

## Verify

```bash
cd HPCPerfStats/monitor
./scripts/build_static_bundle.sh   # or SKIP_DEPS=1 PREFIX=...
cd .build-static && make check && make distclean
./scripts/cross_compile_test.sh --force-foreign --fail-fast TARGETS=x86_64-linux-gnu
```

- [ ] `python3 scripts/check_emitted_variable_names.py` (via `make check` / `check-local`)
- [ ] `scripts/check_unsafe_c_patterns.sh` — remove this file from allowlist when clean

## PR description

1. **Summary of Changes** — safety, splits, style, renames.
2. **Dependencies / UB / warnings** — strict aliasing, signal handlers, third-party APIs.
3. **Test plan** — drivers touched.
