# HPCPerfStats monitor tests

This directory holds **automated tests** for the monitor package. For package layout, build flavors, and key `src/` modules, see **[../README.md](../README.md)**.

Production sources stay in `../src/`; tests are small drivers that compile and link selected `.c` files from `src/` (see `Makefile.am`).

## Layout

| Artifact | Role |
|----------|------|
| `test_assert_live.c` | Built with `-DNDEBUG`; fails if `assert()` is compiled out (see `test_assert_live.h`) |
| `test_dict.c` | `dict_*` hash table (init/set/ref/remv/for_each; null/zero/corrupt-table guards) |
| `test_stats_sentinel_catalog.c` | Value sentinel catalog (`stats_sentinel.h`): codes contiguous from 1 and <= 255, released slugs append-only, unknown codes have no slug |
| `test_stats_set_missing.c` | Value sentinel state byte (`stats.c`): new rows start `NOT_UPDATED`, `stats_set` / `stats_inc` clear to OK, `stats_set_missing` keeps `s_val`, `stats_set_missing_all` / `stats_type_set_missing_all` fill only unset keys, per-cycle `stats_type_mark_not_updated` reset honours the collect tier |
| `test_collect_sentinels.c` | `collect.c` helpers set sentinels instead of skipping or storing 0: bad / missing token `READ_FAILED` (prior value kept), absent file or key `NOT_PRESENT`, `EACCES` `PERMISSION_DENIED`, `path_collect_single` return codes, errno mapping |
| `test_ib_sentinels.c` | `host_ib` / `host_opa` sentinels over a fake sysfs root: missing counter `NOT_PRESENT`, unparsable `READ_FAILED`, unreadable counters dir `READ_FAILED`, MAD `BACKEND_ERROR` / `BACKOFF` / `NOT_BUILT`, OPA STL-only keys get the MAD code |
| `test_likwid_pmc_sentinels.c` | x86 LIKWID `host_cpu_hw`: unprogrammed Intel `fp_arith_*` `NOT_BUILT` every tick, foreign vendor / arm keys `NOT_PRESENT`, fake LIKWID `readGroupCounters` fail `BACKEND_ERROR`, `setupCounters -EBUSY` `PMU_BUSY`, NaN `INVALID_VALUE` |
| `test_gpu_power_sentinels.c` | DCGM per-field status (`NOT_SUPPORTED` `NOT_PRESENT`, else `READ_FAILED`) on the row miss list, RAPL read fail mapped from errno (`EACCES`/`EPERM` `PERMISSION_DENIED`, else `READ_FAILED`) and absent domain `NOT_PRESENT`, hwmon pread failure `READ_FAILED` |
| `test_proc_prune_vanished.c` | `stats_type_prune_untouched`: `host_proc` (`st_prune_untouched`) drops rows a cycle did not touch (exited PID, checked end to end on the real `/proc` as non-root) and re-creates them on return; rows with a code (cooldown `BACKOFF`) and phases with no active key are kept; real-device types keep untouched rows at `NOT_UPDATED` |
| `test_lustre_osc_shared_sum.c` | `lustre_osc` mount row shared by several OSCs publishes the sum of the current OSC lifetimes on every tick (the old `stats_inc` path doubled it on tick 2); an unreadable OSC marks the whole row with its errno code |
| `test_lustre_beegfs_zero_fill.c` | `lustre_mdc` / `lustre_osc` readable stats files publish 0 for omitted counters and `NOT_PRESENT` when absent; BeeGFS ctl keys zero-fill when both clientstats lines were selected, `BACKEND_ERROR` otherwise, and ticks skipped by the refresh gate re-publish the cached values and codes |
| `test_uncore_unprogrammed_not_built.c` | Intel IMC / AMD DF: keys the eventset does not program (SKX `dram_act_count`, SPR `hbm_*` on mbox rows and `dram_*` on hbm rows, Rome `dram_chan2/3_bytes`) emit `NOT_BUILT` on each programmed device; bad eventsets are rejected |
| `test_debug_shm_sentinels.c` | Built with `-DDEBUG` in every build: per-key provenance of `stats_set_missing*` (site `file:line`, errno, first / last cycle, run length), `_all` forms record only the keys they mark, the site registry hit counts (a never-run site counted), the `debug_fault_inject` table (`point:subject!errno@ticks`), and the 0600 `/dev/shm` `sentinels` / `sentinel_sites` files |
| `test_fault_inject_matrix.c` | Built with `-DDEBUG` in every build: forces every failure site of the linked collectors (`collect.c` helpers, `host_cpu`, `host_ps`, `host_vfs`, `lustre_osc`, `lustre_mdc`) through the `open` / `opendir` fault points or synthetic input, checks the code per key and that values resume after release on non-latching reads, and FAILs on any registered site in the binary that never fired |
| `test_fault_inject_hw.c` | Built with `-DDEBUG` (and forced IB / DCGM dlopen defines) in every build: the hardware fault points over the fakes, `perf_open` EBUSY / EACCES (fake perf; PMU_BUSY / PERMISSION_DENIED on just those events, back after re-begin), `mad_query` (MAD test hooks; BACKEND_ERROR on the MAD keys with sysfs keys untouched, BACKOFF once the fail streak trips, values after release), `dcgm_fetch` (DCGM test hooks; BACKEND_ERROR on every GPU key, re-prepare and read after release), `hwmon_read` (fake hwmon tree; READ_FAILED on that meter only); FAILs on any linked site never fired |
| `test_fault_inject_fs.c` | Built with `-DDEBUG`: host_proc (`scandir` point, then cooldown BACKOFF), host_net (`net_rebuild_slow` point, rebuild-skip BACKOFF), host_tmpfs and beegfs_client (missing / latched mounts fixture via the `*_test_set_mounts_path` seams, `statfs` point, failed statvfs, republished ctl code), lustre_llite (`lustre_llite_collect_obd` on missing stats / capacity files) and amd_gpu (`amd_gpu_publish_not_built`); every site in the binary must fire (`test_site_coverage.c`) |
| `test_fault_inject_cpu.c` | Built with `-DDEBUG`: `cpu_counter_metrics.c` with stubbed backends; LIKWID x86 marks every CPU row with the prepare code, the arm backend marks unset keys NOT_PRESENT (each build compiles its own site) |
| `test_fault_inject_{opa,intel_gpu,xpumcli,likwid_pmc,power,arm_publish,uncore}` | Coverage twins: the `test_opa_sysfs_map`, `test_intel_gpu_publish`, `test_intel_gpu_xpumcli_parse`, `test_likwid_pmc_sentinels`, `test_gpu_power_sentinels`, `test_cpu_counter_arm_publish` and `test_uncore_unprogrammed_not_built` sources rebuilt with `-DDEBUG` plus `test_site_coverage.c` (exit 1 when a registered failure site never fired) |
| `test_fault_ci_files.sh` | Automake script test: `scripts/check_fault_ci_files.py` keeps `scripts/lib/fault_ci_files.py` equal to the src files with failure sites that the `test_fault_inject_*` targets link, requires each target to enforce coverage, and fails on any src file with a site outside CI; missing / extra entries FAIL |
| `test_dcgm_session.c` | DCGM session refcount (single Init, shutdown on last release); embedded start denies the NVSwitch module and falls back to v1 when v2 fails |
| `test_arm_imc_scf.c` | Grace SCF for `arm_aarch64_imc` by direct perf (fake perf layer): CAS units (reads = 32 B beats / 2, writes = bytes / 64), two sockets (Horizon / Vista Grace-Grace) and one (GH200), scaled reads, no PENDING mode, no-PMU re-scan then attach, bad entries / open failure skip one socket |
| `test_perf_util.c` | `perf_util`: sysfs PMU `type` / `events/` / `format/` parse, `cpumask` first CPU, enabled/running scaling (seed, Δrunning 0, drop re-seeds), short reads (finding #5: unscaled reads were ~4× low), Δenabled / Δrunning returned with every non-reseed read (3.1 Phase 1b, D6) |
| `test_arm_core_perf.c` | `arm_core_perf`: MIDR from sysfs / `/proc/cpuinfo`, sysfs names first then the Neoverse V2 raw table, unknown MIDR skips FP events, per-CPU scaled lifetimes, failed opens log once (finding #8); PMU sharing counters (3.1 Phase 1b, D6): own oversubscription only (solo = running), outside holder (running below solo), unknown MIDR publishes no solo key, no enabled time publishes nothing |
| `test_fake_perf.c`, `test_fake_perf.h` | Shared test layer: TMPDIR sysfs tree plus a fake `perf_event_open` whose fds are pipes fed `{value, enabled, running}` samples |
| `test_dcgm_watch_policy.c` | `dcgm_watch_policy_*`: refresh follows the collect interval (60 s → 60,000,000 µs, keep 120 s / 2 samples), clamps 1 s–1 h, fallback when unset/invalid, NULL out |
| `test_stats_type_get.c` | `stats_type_get` finds every `st_name` in registry (legacy variable-name) order, so `disable_types` works for renamed types; unknown/NULL return NULL |
| `test_host_key_alias.c` | `host_key_alias_lookup` / `host_key_alias_emit` |
| `test_proc_status_order.c` | `host_proc` deferred Uid/Vm*/Threads flush; Name: deny list |
| `test_proc_filter.c` | `/proc` pid filter denylist; NULL NSS name must not exclude |
| `test_job_proc_discover.c` | Shared-node jid discovery (`slurmstepd.scope` + `SLURM_JOB_ID`, then root stepd title for idle allocations; fake `/proc` + `HPCPERFSTATS_CGROUP_ROOT`) |
| `test_job_gpu_assign.c` | `SLURM_JOB_GPUS` / AutoDetect grant mapping (c122-018 minor map hooks) |
| `test_job_sched_affinity.c` | `sched_getaffinity` union inject + full-resource GPU equality |
| `test_job_discovery_marks.c` | `%jobs` / `%affinity` wire shape, full-resource predicate, and per-payload row filter (`job_emit_filter.c`: title-named jobs never emit `%affinity`) |
| `test_schema_emit.c` | `stats_format_emit_*` banner/schema/mark helpers |
| `test_procfile_kv.c` | `proc_kv_into_stats` (header-only) |
| `test_collect_str.c` | `str_collect_key_list` / `str_collect_prefix_key_list` |
| `test_cpu_counter_arm_util.c` | `arm` backend helpers (`cpu_counter_metrics_arm_util.c`, portable): jiffy util, `/proc/stat` line parse, cpufreq clock (`scaling_cur_freq` via pread, nominal chain, never `cpuinfo_cur_freq`) over fixture sysfs trees |
| `test_cpu_counter_arm_publish.c` | `arm` publish/accumulate (util µs, util×freq cycles; no `dcgm_cpu_power_*` keys, D33/D36); never synthesizes instr / FLOPs (O15); measured CYC overwrites the three cycle keys only when > 0 (O16); D1 keys, W=48 modulo emit, `arm_est_flops` = FIXED + SCALE |
| `test_arm_power_hwmon.c` | ACPI `<Name> Power Socket <N>` meter index: label parse, EL10 `hwmonN/` and EL9 `hwmonN/device/` layouts, GB200 / GH200 / Grace-Grace label sets, shuffled hwmon numbers, unknown label, missing / garbage value, duplicates |
| `test_arm_power_socket.c` | `host_power_socket` schema keys and per-socket publish (µW → rounded W, absent meter → 0, equal sockets both kept) |
| `test_ib_schema_contract.c` | `ib.h` KEYS rename contract |
| `test_host_schema_contract.c` | Host collector schema token contract |
| `test_lustre_schema_contract.c` | Lustre collector schema token contract |
| `test_lustre_proc_stats.c` | Modern/legacy Lustre stats line parse + file open order; header lines (`snapshot_time <epoch> secs.nsecs`) are not counted as samples |
| `test_opa_schema_contract.c` | OPA collector schema token contract (`host_opa.h`) |
| `test_opa_sysfs_map.c` | hfi1 classic + hw_counters filename → KEYS; classic-miss → hw fallback |
| `test_opa_mad_backoff.c` | OPA MAD failure backoff |
| `test_ib_hca_is_opa_hfi.c` | HFI vs IB HCA classification |
| `test_opa_lspci_match.c` | Stampede3 Cornelis/OPA lspci fixture match |
| `test_intel_gpu_schema_contract.c` | Intel PVC `intel_gpu` KEYS contract (Xe Link, no NvLink) |
| `test_intel_gpu_publish.c` | libxpum path publish + default xpumcli skips `xpumInit` |
| `test_intel_gpu_xpumcli_parse.c` | xpumcli discovery/dump CSV parse → KEYS |
| `test_xpum_gpu_dyn.c` | XPUM dlopen stubs / missing-lib / hooks |
| `test_lnet_schema_contract.c` | LNet collector schema token contract |
| `test_likwid_rapl_support.c` | Intel vs AMD RAPL processor support gates + vendor collect path (SPR/EMR/GNR/SRF ≠ AMD; EPYC enums) |
| `test_likwid_rapl_pwr_events.c` | PWR* eventset strings + schema key map + J→mJ for PERF RAPL |
| `test_intel_rapl_likwid_ready.c` | `intel_x86_rapl` begin requires LIKWID ready + PWR begin (DIRECT env does not skip PWR) |
| `test_amd64_rapl_likwid_ready.c` | `amd_x86_rapl` begin requires LIKWID ready + PWR begin (DIRECT env does not skip PWR) |
| `test_amd_cpuid_match.c` | EPYC Rome/Milan/Genoa/Turin CPUID allowlists; Ryzen/unknown → `-1` |
| `test_amd_df_likwid_profiles.c` | Golden LIKWID DF `EVENT:COUNTER` strings per EPYC gen |
| `test_amd_x86_uncore_df_schema.c` | Family DF `st_name` + `dram_chan*_bytes` schema contract |
| `test_amd_legacy_removed.sh` | Legacy `amd64_pmc`/`amd64_df` sources gone; registry has family DF only |
| `test_intel_legacy_pmc_removed.sh` | Legacy Intel MSR gpr4/8 / `msr_io` / `fallback_fill` / DIRECT `power_read` gone |
| `test_metric_profiler_link_stubs.sh` | Every driver linking `src/stats_runtime.c` without `src/metric_profiler.c` stubs all four profiler hooks, so `make check` links with `--enable-metric-profiler` |
| `test_prepare_rpmbuild_measure_flag.sh` | `prepare_rpmbuild_dirs.sh --measure-overhead`: exclusive with `--debug-build`, fails before building on prompting sudo / missing tools, `--measure-arg` passthrough, single-RPM discovery, default footer unchanged |
| `test_string1.c` | `wsep` / `strsep_ne` (header-only `string1.h`) |
| `test_stats_buffer_data_append.c` | `stats_buffer_data_append` (RMQ payload string growth) |
| `test_stats_buffer_uts.c` | `stats_buffer_ensure_uts_cached` / `stats_buffer_uts_cache_reset` |
| `test_stats_buffer_rows.c` | `stats_buffer_append_enabled_type_rows` (tier row assembly into `sf_data`) |
| `test_stats_buffer_collect.c` | `stats_buffer_collect` fast/full tier payloads (`STATS_BUFFER_TEST_SEND_HOOK`; RabbitMQ build) |
| `test_stats_buffer_debug_shm.c` | DEBUG shm write path (`fast`/`full` overwrite, legacy→full) |
| `test_job_shared_emit_debug_shm_mirror.sh` | Shared-node: trailing node-wide `-` must not mirror into DEBUG shm |
| `test_ib_mad_dlopen_link_deps.sh` | `IB_MAD_DLOPEN` test programs link `dyn_lib_path.c`; DEBUG shm tests link `secure_path.c` |
| `test_debug_shm_emit_golden.c` | Golden regression for assembled `@fast`/`@full` sample payloads via debug shm (`tests/expected/debug_shm_*.txt`; DEBUG + RabbitMQ) |
| `test_debug_shm_schema_mirror.c` | `schema` shm file + `stats_buffer_debug_shm_schema_wanted` gating (DEBUG) |
| `test_shm_message_correctness.sh` | Automake script test (wrapper `test_shm_message_correctness`): Python validator on `tests/expected/synthetic_fixture/` (no `$sentinels` banner: sentinel layers report `SKIP`, never `PASS`), a sent1 variant (banner + catalog tokens counted per code; `-99` rejected), and the cross-sample fixture |
| `test_shm_validation_lib.sh` / `.py` | Automake script test (wrapper `test_shm_validation_lib`): unit tests of `scripts/lib/` validators, incl. sentinel catalog drift vs `src/stats_sentinel.h` and the consumer bridge set, `token_is_value`, the `$sentinels` banner layer, and cross-sample bridge validity (chain-breaking gaps exempt, bridgeable decrease FAIL, LIKWID `host_cpu_hw` informational) |
| `test_check_sentinel_error_paths.sh` | Automake script test: `scripts/check_sentinel_error_paths.py` is clean on `src/` (allowlist `scripts/check_sentinel_error_paths.allowlist`), flags `stats_set(..., 0)` and a silent `continue` / `return` / `goto` (optionally after logging) after a failed read (direct or `f = fopen(...); if (f == NULL)`), ignores sentinel paths, comments and non-collector files, and rejects stale or reasonless allowlist entries |
| `test_independent_read_check.sh` | Automake script test: `scripts/lib/independent_read.py` (a live counter read taken between two captures, from `/proc/stat`, IB / OPA counters or `/sys/block/<dev>/stat`, must lie in `[v_a, v_b]` mod 2^W; a gauge from node `meminfo` or the hwmon power meters FAILs only when emitted 0 in both captures while the source is non-zero; a read after sample B is inconclusive) and the `capture_pair` between-captures hook (retried when a sample lands during the read) |
| `test_consumer_parse_shm_capture.sh` | Automake script test (SKIP 77 without the repo `.venv`): `scripts/consumer_parse_check.py` feeds a sent1 capture through the consumer parser; sentinel rows parse as `-n` with no delta, no negative delta, Codon parity when the extension is built; a sentinel row the consumer drops FAILs |
| `test_fault_inject_verify.sh` | Automake script test: `scripts/fault_inject_verify.sh` + `fault_inject_check.py` against a stub daemon (a fake shm writer the restart command relaunches): PASS on a clean run with the conf restored, FAIL when a key outside the injected point changes code, NOTE for a never-fired site of an inactive collector, SKIP with `FAULT_INJECT=0` and on a release build; `bash -n` on both verify scripts |
| `test_shm_message_golden.sh` | Automake script test (wrapper `test_shm_message_golden`): build-slug goldens `tests/expected/shm_*_<slug>.txt`; SKIP (exit 77) when none exist for the slug |
| `scripts/emit_build_capabilities.py` | Writes `monitor-build-capabilities.json` + `capability_slug` and `MONITOR_CAPABILITY_SLUG` header (`make capabilities` / `BUILT_SOURCES`) |
| `scripts/build_message_expectations.py` | Host probes + shm `schema` → `expectations_<slug>.json` |
| `scripts/validate_shm_messages.py` | Validates shm `schema`/`fast`/`full` against expectations manifest; live `--cross-sample-check` injects brief host stimulus by default (`--no-cross-sample-stimulus` to disable) |
| `test_stats_runtime_collect.c` | `stats_runtime_collect_cycle`, `stats_schema_key_active_this_phase` |
| `test_path_collect.c` | `path_collect_single` / `path_collect_list` (`open`/`read` path I/O) |
| `test_path_read.c` | `path_read_small` / `path_read_alloc` (incl. `PATH_READ_ALLOC_MAX`, NULL guards) |
| `test_sys_iter.c` | `sys_iter_for_each` (sysfs/sys iteration helper) |
| `test_procfile_parse.c` | `procfile_parse_ws`, `proc_kv_into_stats` |
| `test_monitor_log.c` | `monitor_log_*` facade |
| `test_monitor_options_kv.c` | `monitor_options_apply_daemon_conf_kv` (NULL/unknown/empty keys) |
| `test_pscanf.c` | `pscanf` (`open`/`read` + `vsscanf`) and `file_fopen_read` |
| `test_schema_parse.c` | `parse_schema_entry`, `schema_init` / `schema_destroy` (links `schema.c`, `dict.c`) |
| `test_stats_text_format.c` | Schema suffix helpers + **`stats_format_append_mark_va`** (mark concatenation) |
| `test_ib_port_state.c` | `ib_port_logic_active` / `ib_port_phys_link_up` sysfs line parsers |
| `test_ib_port_collectible.c` | `ib_port_collectible` NULL guard (sysfs path hardcoded) |
| `test_ib_foreach_hca_port.c` | `ib_foreach_hca_port` NULL-callback guard |
| `test_ib_family.c` | `ib_family_disable_all` |
| `test_ib_mad_backoff.c` | `ib_mad_*_collect_cycle_ok` fresh-state allowance (Infiniband build) |
| `test_ib_mad_decode.c` | `ib_mad_ext_decode_counters` with synthetic MAD buffer (Infiniband build) |
| `test_hwdetect_lspci.c` | `hwdetect_invalidate_probe_cache` + probe smoke |
| `test_cpu_counter_metrics_schema.c` | `CPU_COUNTER_METRICS_KEYS` schema tokens; retired `dcgm_cpu_power_*` keys absent; `arm_fp_{dp,sp,hp}_spec` `E,W=64` on arm builds only |
| `test_gpu_schema_contract.c` | NVIDIA/AMD GPU schema substrings (incl. module/sysio power keys) |
| `test_nvidia_gpu_estimate.c` | `nvidia_gpu_estimate_rates` FLOP/memory-bandwidth math |
| `test_nvidia_gpu_dcgm_field.c` | DCGM blank sentinels and blank-family reject on GPU watts/util/u64/ratio converters |
| `test_nvidia_gpu_dcgm_profiles.c` | FieldGroupCreate profile ladder (full / core / basic); no profile watches 1132/1133; attempt order |
| `test_nvidia_gpu_nvml_convert.c` | NVML / GPM converters: percent × 0.01, MiB/s × 1048576, mW → W, bytes → MiB (values from the GB200 load check) |
| `test_nvidia_gpu_nvml.c` | NVML + GPM source with a stubbed NVML table: all-GPM → NVML, any non-GPM GPU / init failure / no GPU → DCGM, GPM pair and short-gap handling, per-metric failure, mid-run GPM failure, `HPCPERFSTATS_NVML_LIB` allowlist |
| `test_roofline_hw_peak_schema.c` | Roofline peak schema contract strings |
| `test_roofline_hw_peak_changeover.c` | Roofline changeover / mode gating with `HPCPERFSTATS_SKIP_HW_PROBE` |
| `test_roofline_detect_fixture.c` | `roofline_hw_peak_detect_fill_cache` fixture when `HPCPERFSTATS_SKIP_HW_PROBE=1` |
| `test_roofline_hw_peak_identity.c` | GB200/GH200/Grace identity tables + Horizon/Vista smi CSV parsers |
| `test_daemonize.c` | `daemonize()` double-fork and PID lock file (subprocess tests) |
| `test_nfs_schema_subset.c` | NFS collector schema tokens vs. `nfs.c` |
| `test_nfs_mountstats_parse.c` | `host_nfs` parse of `fixtures/nfs/mountstats_v3_v4.txt`: one row per `nfs` and `nfs4` mount, other fstypes skipped; `delay` = events field 24, `xprt_bad_xids` / `req_u` / `bklog_u` after sends and recvs (tcp, udp, rdma), `*_ops` = per-op field 0; truncated lines set nothing |
| `test_metric_profiler.c` | Metric profiler aggregation/report smoke test (enabled and disabled builds); `metric_profiler_report` flushes a short run (daemon shutdown report) |
| `test_monitor_cli.c` | RabbitMQ daemon CLI (`monitor_cli_*`), usage sentinel strings, `-h` via subprocess |
| `test_ring_buffer.c` | Ring buffer / `stats_buffer_collect` tier-row assembly (`STATS_BUFFER_TEST_SEND_HOOK`; RabbitMQ build) |
| `test_monitor_cli_globals.c` | Stub globals for `test_monitor_cli` |
| `test_rabbitmq_integration.sh` | Rootless end-to-end monitor -> RabbitMQ publish validation |
| `scripts/bootstrap_local_rabbitmq.sh` | User-space RabbitMQ bootstrap/start/stop helper (must be in the source tree for `make dist` / RPM prepare) |
| `rmq_integration_validate.py` | Consumes queue messages and validates listend-compatible payload shape |
| `requirements-rabbitmq-integration.txt` | Python dependency pin for integration validator (`pika`) |
| `test_monitor_configure_help.sh.in` | Regression (via `check-local`): `configure --help` mentions `--enable-all-static`, `--enable-metric-profiler`, and `--with-metric-profiler-backend`; must **not** mention `--enable-legacy-pmcs` |
| `test_likwid_read_group_counters.sh` | Regression (via `check-local`): PMC/uncore adapters call `perfmon_readGroupCounters` only (not bare `perfmon_readCounters`, which follows stolen `activeGroup`) |
| `test_likwid_static_pin.sh` | Regression (via `check-local`): `build_static_bundle.sh` and `cross_compile_test.sh` pin LIKWID **5.5.2** (not a `5.5.2rc*` prerelease) |
| `test_script_word_lists.sh` | Regression (via `check-local`): string flag / package lists (`CC`, `CPPFLAGS`, `LDFLAGS`, `LIBS`, `CFLAGS`, `CXXFLAGS`, `ROCKY9_RPM_TOOLCHAIN_PACKAGES`) are split on whitespace but never glob-expanded; runs the real `build_static_bundle.sh` release-flag, `prepare_rpmbuild_dirs.sh` LIKWID-probe and `cross_compile_test.sh` fingerprint functions next to a `-Iinjected` file, and pins the default package-list fingerprint so cached toolchain layers stay valid |
| `test_likwid_on_aarch64_configure.sh` | Regression (via `check-local`, 3.1): non-x86 `auto` is the `arm` backend with no LIKWID overlay; `--with-cpu-counter-backend=likwid` is rejected on non-x86; bundle / prepare / cross build LIKWID for x86 only |
| `test_canary_gemm_load.sh` | `check-local` (skips with 77 without a C compiler with OpenMP): `scripts/canary_gemm_load.sh` rejects bad options before building, builds dp/sp, a bounded foreground run reports GFLOP/s with a finite check value and ends with its exact `total_flops` (a multiple of 2n³), background start / refused second start / status / stop / idle stop |
| `test_canary_counter_check.sh` | `check-local` (~1 min): `scripts/canary_counter_check.sh` against a fake debug-shm writer and fake `perf`: header from the first non-blank line, bounded waits, O3 PASS/FAIL, `<not counted>` slots kept as NA, `--scf` mode, and the Phase 1b sharing line (solo 77.8 / 77.8 / 0.0, outside holder 27.8 / 77.8 / 64.3, "not published" on a daemon built before Phase 1b), and `--sharing-only` (no load, no perf: quiet 77.8 / 77.8 / 0.0, outside holder 64.3%, FAIL without the keys) |
| `test_perf_raw_flops_check.sh` | `check-local` (~2 s): `scripts/perf_raw_flops_check.sh` against a fake perf, GEMM load and cpuinfo: Intel / AMD default events, weighted ratio PASS, precision leak FAIL, AMD undercount FAIL, events perf cannot open skipped (perf exits 0 on `<not supported>`), `<not counted>` in a run, multiplexed running share shown, missing GEMM total, all-zero hint, `--list` encodings, bad options exit 2 |
| `test_run_warning_gate.sh` | `check-local` (<1 s, no build): `scripts/run_warning_gate.sh` contracts: `--parse-gc-log` keeps only `.text.*` sections dropped from `hpcperfstatsd-*.o` in GNU ld and lld output (clone suffixes stripped, dedup), `--dead-from-sets` reports a function only when every configuration that defines it leaves it unreachable (ARM-only / DEBUG-only callers count as reached), a missing prerequisite prints the bold SKIPPED banner and exits 0, `HPC_WARN_GATE_REQUIRE=1` exits 1, bad options exit 2 |
| `Makefile.am` | Automake `check_PROGRAMS` / `TESTS`; **keep `monitor_unit_cppflags` in sync** with `src/Makefile.am` `hpcperfstatsd_CPPFLAGS` for `-D` flags |
| `run_tests.sh` | Convenience wrapper around `make check` in a build directory |
| `../scripts/profile_hpcperfstatsd_example.sh` | Prints `perf record` / `perf stat` recipes for CPU baseline comparisons |

## How to run

**Recommended (full build + tests):**

```bash
./scripts/build_static_bundle.sh
make -C .build-static check
```

**Using the runner** (defaults to `.build-static` under the monitor package):

```bash
./tests/run_tests.sh
./tests/run_tests.sh /path/to/your/build-tree
```

**Plan-close extras** (additive to arch-scoped dual-verify; **monitor-valgrind-cpp-linter-gate** — no git hooks):

```bash
./scripts/run_valgrind_check.sh
./scripts/run_cpp_linter.sh
# clang tools: .venv/bin/pip install -r tests/requirements-cpp-linter.txt
```

**After `configure` + `make` in a build directory:**

```bash
make check
```

Run from the **configured build tree root** (not only `build/src`). The suite lives in the `tests/` subdir of the build mirror.

## RabbitMQ integration test (rootless, no Docker)

This test path installs/starts RabbitMQ in user space, runs `hpcperfstatsd` against it, then validates consumed messages for:

- UTF-8 decodability
- listend-compatible host parsing (`$` payload host token and sample payload third token)
- AMQP publish properties (`content_type=text/plain`, `delivery_mode=2`)

Run explicitly:

```bash
make -C .build-static check-rabbitmq-integration
```

Or run with `check-local` by opting in:

```bash
RUN_RMQ_INTEGRATION=1 make -C .build-static check
```

### Debug shm golden regeneration

When intentional changes alter sparse row assembly or tier tokens, refresh the checked-in expected files:

```bash
UPDATE_DEBUG_SHM_GOLDEN=1 make -C .build-static check TESTS=test_debug_shm_emit_golden
```

Requires a **DEBUG** configure (`--enable-debug`). Non-DEBUG builds skip this test.

Environment knobs:

- `INSTALL_ROOT` (default `~/.cache/hpcperfstats-rmq`) for downloaded/built Erlang + RabbitMQ artifacts.
- `ERLANG_HOME` and `RABBITMQ_HOME` to reuse preinstalled user-space trees and skip downloads/build.
- `SKIP_DOWNLOAD=1` to forbid network fetches (requires local archives or provided homes).
- `ERLANG_ARCHIVE_SHA256` / `RABBITMQ_ARCHIVE_SHA256` for archive integrity checks.
- `MIN_MESSAGES`, `VALIDATE_TIMEOUT_SECONDS`, `MONITOR_WARMUP_SECONDS` to tune runtime/strictness.

## Adding a test

1. Add `test_foo.c` (and any helpers) under `tests/`.
2. Register the program in `tests/Makefile.am`: `check_PROGRAMS`, `test_foo_SOURCES`, and `test_foo_LDADD = $(LDADD)` if linking like other unit tests.
3. To compile production code from `src/`, list paths as `$(top_srcdir)/src/your_unit.c`.
4. Run `autoreconf -fi` in the monitor root if you change `configure.ac`, then reconfigure and `make check`.

Project rules: **monitor-c-new-function-unittests**, **monitor-c-testing-standards**, and **global-testing-discipline**.

## Writing unit tests

Follow **monitor-c-testing-standards** (`.cursor/rules/monitor-c-testing-standards.mdc`):

- **Default to unit tests** — one concern per `test_<module>.c`, link production `.c` from `src/`.
- **F.I.R.S.T.** — fast, independent, repeatable, self-validating (`assert` + non-zero exit), timely (same change as production).
- **AAA** — Arrange → Act → Assert in each `static void test_*()` helper; reset globals in `main()` between groups.
- **Cover contracts** — return values, NUL termination, emitted keys/strings; at least one NULL/bounds and one error path per exported function.
- **Stub `stats_set`** — use `test_stats_stub.c` / `test_stats_stub_bind()` when linking units that emit stats without full `stats.c`.
- **Register** every driver in `Makefile.am` and this layout table.
- **`assert()` stays live in release builds** — `AM_CPPFLAGS` force-includes `test_assert_live.h` (`#undef NDEBUG`), so drivers may put required calls inside `assert()` even when `HPC_BUNDLE_RELEASE_BUILD=1` adds `-DNDEBUG`. Keep `$(AM_CPPFLAGS)` in any per-target `*_CPPFLAGS`.

## Configure-sliced tests

Some drivers are omitted or skipped depending on the configured build:

| Condition | Affected tests |
|-----------|----------------|
| (always) | `test_cpu_counter_arm_util`, `test_cpu_counter_arm_publish`, `test_arm_power_hwmon`, `test_arm_power_socket` (portable arm-backend / hwmon code; runs on x86 hosts too) |
| (always) | `test_likwid_rapl_support` links production `likwid_rapl.c` (PWR collect stubs allowed; support asserts do not require live LIKWID) |
| `RABBITMQ` | `test_monitor_cli`, `test_ring_buffer`, `test_stats_buffer_collect`, `test_stats_buffer_debug_shm`, `test_debug_shm_emit_golden`, `test_monitor_timing` |
| `DEBUG` | `test_stats_buffer_debug_shm`, `test_debug_shm_emit_golden` (meaningful assertions; otherwise skipped) |
| `INFINIBAND` | `test_ib_mad_backoff`, `test_ib_mad_decode` |
| Live broker | `test_rabbitmq_integration.sh` — opt-in via `RUN_RMQ_INTEGRATION=1` or `check-rabbitmq-integration` |
