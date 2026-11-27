# Release notes

This page records the one-time cutovers a running site applies once, when it upgrades past the release that introduced them.
It is for operators upgrading an existing stack; recurring upgrade procedures are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md).

Each entry is dated by the commit that introduced the change.
New entries go at the end; existing entries are not rewritten.

## 2026-09-06: Cluster syslog on the data volume

Cluster syslog lives under the single data volume.
If a site mounted a separate host path for node logs (for example `/opt/hpcperfstats_log` at `/hpcperfstatslog/`), copy any `cluster.log` it needs into `/data/hpcperfstats_data/site_data/logs/current/` (or the site's `hpcperfstatsdata` device), then drop the extra compose volume.
Delete a local `docker-compose.app.yaml`: the app overlay is obsolete, and the stack uses the site `docker-compose.yaml` and the base compose only.

## 2026-09-17: Rootless Podman runtime

The stack runs under rootless Podman with storage under `/data`.
To move an existing root-runtime host:

1. Stop the old stack without a global prune.
2. Install DNF-managed Podman, `podman-compose` and Node 24.
3. Allocate non-overlapping `/etc/subuid` and `/etc/subgid` ranges large enough to map container uid 901860.
4. Create and own `/data/user/<SERVICE_USER>/{cache,tmp,tools}` and `/data/podman/<SERVICE_USER>/{storage,images,volumes,cache,tmp}`.
   Move no active container storage in place.
5. Configure the rootless `~/.config/containers/storage.conf` with graphroot `/data/podman/<SERVICE_USER>/storage` and imagestore `/data/podman/<SERVICE_USER>/images`, and `containers.conf` with the `/data` volume and image-copy temporary paths.
6. Run `podman system migrate`, then check `podman info` before rebuilding.
7. Point npm, pip, XDG, Playwright and temporary paths below `/data/user/<SERVICE_USER>`; remove the old root-filesystem caches only after the new paths work.
8. Rebuild with `./scripts/rebuild_full_site.sh`.

`<SERVICE_USER>`: the deployment account.

Development uses project `hpcperfstats-dev` (`HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-dev`) on ports 8080, 8443 and 1514; tests use `hpcperfstats-test`.
Never run a global Podman prune or enable an alternate runtime-compatibility endpoint.
The OCI file names (`.dockerignore`, `docker-compose*.yaml`) and the service bake recipes under `services-conf/*.Dockerfile` (web and pipeline: `services-conf/pipeline_web.Dockerfile`, context `./`) keep their names.
The fresh-host setup is in [Install HPCPerfStats](../tutorials/install-hpcperfstats.md).

## 2026-10-01: Process-tree roof in cgroup percent

The pipeline reads the tree RSS roof as a percent of runtime `memory.max`.
Remove `sync_process_tree_rss_limit_mb` and `sync_process_tree_rss_exit_mb` from the baked site INI; nothing reads them.
Add under `[PIPELINE]`:

| Old key                                | New key                                     | 128g production default                                                 |
| -------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------- |
| `sync_process_tree_rss_limit_mb=50000` | `sync_process_tree_rss_limit_cgroup_pct=40` | 40: effective roof 52428 MiB, raw budget about 52428 MiB (`/ PEAK 1.0`) |
| `sync_process_tree_rss_exit_mb=0`      | `sync_process_tree_rss_exit_cgroup_pct=0`   | 0: hard exit off                                                        |

Effective MiB is `(memory.max x pct // 100) // (1024 x 1024)` at runtime.
`limit_cgroup_pct=0` disables defer and the raw-byte admit gate, as `limit_mb=0` did.
On a host whose cgroup is not 128g, compute `round(100 x desired_roof_mib / memory_max_mib)`.
Telemetry prints `rss_limit_cgroup_pct=` and the effective `rss_limit_mib=` ([Tree RSS roof](../explanation/pipeline-concurrency.md#tree-rss-roof)).

## 2026-10-02: File-cache admit in cgroup percent

Remove `sync_cgroup_admit_max_file_cache_mib` from the baked site INI; nothing reads it.
Add `sync_cgroup_admit_max_file_cache_cgroup_pct` under `[PIPELINE]` (default 60; `0` turns the gate off); a site that set `sync_cgroup_admit_max_file_cache_mib=81920` (or any MiB) uses 60, about 78643 MiB on a 128g cgroup.
The effective MiB comes from `process_memory.effective_cgroup_admit_max_file_cache_mib()`, with the same formula as the roof.
Telemetry prints `file_cache_cgroup_pct=` and the effective `file_cache_cfg_mib=`.
The page-cache relief on large cgroups is `sync_pipeline_drop_page_cache=yes` plus a restart after deploy, not a lower `sync_ingest_pool_processes` ([Page cache](../explanation/pipeline-concurrency.md#page-cache)).

## 2026-10-05: `archive_zstd_drop_page_cache` renamed

`sync_pipeline_drop_page_cache` (default `yes`) replaces `archive_zstd_drop_page_cache`.
A site that set `archive_zstd_drop_page_cache = no` needs `sync_pipeline_drop_page_cache = no`, or the hints turn back on.
`scripts/rewrite_hpcperfstats_ini.sh` migrates it, also from `[DEFAULT]`, and prints `renamed: [PIPELINE] archive_zstd_drop_page_cache -> sync_pipeline_drop_page_cache`; when both keys are set, the new one wins and the old one is reported dropped.

## 2026-10-31: PostgreSQL over the shared Unix socket

`web` and `pipeline` connect to the PG15 `db` service through a Unix socket on the `postgres_runtime` tmpfs named volume (`/run/postgresql`), not TCP.
TCP 5432 stays published on `hpcperfstats_net` for everything else.
`db` loads `services-conf/postgres_pg_hba.conf` through `-c hba_file=`, so socket logins need the password (scram-sha-256, as for network logins) instead of the image default `trust`; that file overrides `$PGDATA/pg_hba.conf` on existing data directories.
Loopback (`exec db psql -h localhost`) keeps `trust`; `exec db psql` without `-h` uses the socket and fails with `fe_sendauth: no password supplied` (or a password prompt), so keep `-h localhost` on every in-container `psql` and `pg_dump`.

To cut over:

1. In the site `hpcperfstats.ini`, delete or comment out an old `host = db` line: `/run/postgresql` is the code default for `[DEFAULT] host` (and `5432` for `[DEFAULT] port`, which names the socket file).
   `scripts/rewrite_hpcperfstats_ini.sh` does this.

2. Run `./scripts/rebuild_full_site.sh`.
   It bakes the INI, runs `down --remove-orphans` and `up -d --no-build`, and fails when `web` or `pipeline` cannot see `/run/postgresql/.s.PGSQL.5432` within `HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT` (default 300 s).
   Never recreate only `web` and `pipeline`: with the new INI and an old `db` they wait forever for the socket and still show as running.

3. Check that `hba_file` is `/etc/postgresql/pg_hba.conf` and that socket clients appear once `web` and `pipeline` are up:

   ```bash
   Q="SHOW hba_file; SELECT count(*) FROM pg_stat_activity
     WHERE client_addr IS NULL AND backend_type = 'client backend';"
   podman-compose -p hpcperfstats exec -T db psql -h localhost \
     -U hpcperfstats -Atc "$Q"
   ```

To roll back, set `host = db` again; the socket volume and the hba file are harmless with TCP clients.

## 2026-11-04: Valkey replaces the `redis` service

The Compose `valkey` service replaces `redis` (image `redis` 8.10, Open Source edition), and every project-owned name moved: service, container `hpcperfstats_valkey_1`, volume `valkey_runtime`, socket `/run/valkey/valkey.sock`, and INI key `[CACHE] valkey_location`.
`conf_parser` does not read the old `redis_location` key, so the site INI must go through the rewrite script.
valkey-py and django-valkey replace `redis-py` and Django's built-in `RedisCache`; the image rebuild installs them (`valkey==6.2.0`, `django-valkey==0.5.0`, backend `django_valkey.cache.ValkeyCache`, default pickle serializer).
Both are pure-Python wheels; the optional `libvalkey` C parser extra is not installed, so the free-threaded pipeline interpreter keeps the GIL disabled.
Wheel sha256 for audit: django-valkey `50509c60be59f61f6094720c3e326a22f54c6a3537e4729b9c63aa2e83c69e0b`, valkey `94a12c87cd070e356b2c89e2946fa582d9f65ec2e003ab1867e987220e2beae8`.
The settings override `JOB_PLOT_REDIS_MAX_BYTES` is `JOB_PLOT_VALKEY_MAX_BYTES`; rename it wherever a host sets it.

To cut over, from the checkout holding the site INI, after `git pull` to the release:

```bash
./scripts/rewrite_hpcperfstats_ini.sh --dry-run
./scripts/rewrite_hpcperfstats_ini.sh
./scripts/rebuild_full_site.sh
podman rm -f hpcperfstats_redis_1
podman volume rm hpcperfstats_redis_runtime
```

The dry run prints `renamed: [CACHE] redis_location -> valkey_location`, or nothing for that key when the site never set it.
If it prints nothing while the site INI sets `redis_location`, the checkout is not on the release.
The rename maps the compose socket `/run/redis/redis.sock` to `/run/valkey/valkey.sock`, host `redis` to `valkey`, and `redis://` / `rediss://` to `valkey://` / `valkeys://`; any other host, port, credentials and the db index stay.
`rebuild_full_site.sh` then starts `valkey`, `web` and `pipeline` together on the new socket.
The last two lines are cleanup only: nothing mounts the old container or volume.
The cache starts empty, as after any restart.

## 2026-11-10: RabbitMQ 4.3 to 4.4

The Compose broker image moved from the 4.3 minor to 4.4.
Before the recreate, enable every stable feature flag on the running 4.3 broker and confirm that none is left `disabled` ([Upgrade RabbitMQ to a new minor](../how-to/operate/upgrade-the-stack.md#upgrade-rabbitmq-to-a-new-minor)).

## 2026-11-16: INI keys read only from their own section

`conf_parser` reads each key only from its own section, so a key left in an old section reads as its code default.
PostgreSQL keys belong in `[DEFAULT]`; ingest, archive and metrics keys belong in `[PIPELINE]`.
Before deploying, run `scripts/rewrite_hpcperfstats_ini.sh` from the checkout holding the site INI, check the `--dry-run` diff, then run it for real and rebuild ([Rewrite the site INI](../how-to/operate/upgrade-the-stack.md#rewrite-the-site-ini)).
Upgrading from an older ini layout takes one rewrite: it moves the `[PORTAL]` keys below to their sections, gives pipeline tuning in `[DEFAULT]` a `[PIPELINE]` section and portal tuning in `[DEFAULT]` a `[PORTAL]` section, and keeps every value.

Keys not honored in their old section:

| Old section | Keys                                                                                                                                                                                   | Read only from |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| `[PORTAL]`  | `dbname`, `engine_name`, `username`, `password`, `host`, `port`                                                                                                                        | `[DEFAULT]`    |
| `[PORTAL]`  | `archive_dir`, `acct_path`, `daily_archive_dir`, `archive_keep_uncompressed_tar`, `archive_zstd_level`, `archive_zstd_threads`, `ingest_zstd_threads`, `archive_seal_parallel_workers` | `[PIPELINE]`   |

`[DEFAULT]` values stay visible from every other section that exists (configparser inheritance), so tuning left in `[DEFAULT]` keeps working while the INI has a `[PIPELINE]` or `[PORTAL]` section; without that section it reads as the code default.

## 2026-11-16: Removed INI keys

These keys had no reader and are gone from `conf_parser` and the example.
A leftover line in a site INI has no effect, and `scripts/rewrite_hpcperfstats_ini.sh` drops it (`dropped (not in example): ...`).
None has a replacement unless the table names one.

| Removed key                                                                         | Notes                                                                      |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `metrics_scheduler_prefetch_chunks`                                                 | None                                                                       |
| `sync_codon_context_pool_fraction`                                                  | The Codon slot count is `sync_ingest_pool_processes`                       |
| `sync_archive_queue_max_size`                                                       | None                                                                       |
| `sync_archive_retry_backoff_base_seconds`, `sync_archive_retry_backoff_max_seconds` | None                                                                       |
| `sync_supervisor_rss_limit_mb`                                                      | The memory roof is `sync_process_tree_rss_limit_cgroup_pct`                |
| `sync_enable_ingest_first_durability_mode`                                          | None                                                                       |
| `sync_day_close_manifest_stale_seconds`                                             | None                                                                       |
| `sync_archive_worker_stall_seconds`                                                 | None                                                                       |
| `metrics_scheduler_mode = global_fifo` (value)                                      | Ran the same scheduler as `global_priority` and reads as `global_priority` |

## 2026-11-21: PG18 `host_data` in double precision

On PG18, `host_data.value`, `delta` and `arc` are `double precision`; PG15 keeps `real`, so raw counters above 2^24 lose digits there.
A PG18 database created before this release holds `real` columns and is rebuilt from empty while PG15 is still authoritative ([Migrate to PostgreSQL 18](../how-to/operate/migrate-to-postgresql-18.md#troubleshooting)).
