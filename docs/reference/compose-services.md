# Compose services

This page describes each service in the HPCPerfStats Compose stack: image, role, limits, mounts and the commands that operate it.
It is for operators running a site and developers changing `docker-compose.defaults.yaml`.

All services run on the Compose network `hpcperfstats_net`.
Run every command from the checkout that holds `docker-compose.yaml`.

## Compose files

The stack is defined by three files:

- `docker-compose.defaults.yaml` (tracked): service definitions, healthchecks and stack wiring.
- `docker-compose.yaml` (gitignored, copied from `docker-compose.yaml.example`): `include:`s the defaults file and merges site binds on top (host ports, mem limits, TLS and SSH devices).
- `docker-compose.pg15-primary.yaml` (tracked, legacy sites only): included after the defaults by a site that still runs PostgreSQL 15 ([Migration services](#migration-services)).

`podman-compose -p hpcperfstats` discovers `docker-compose.yaml` automatically, so commands pass no `-f`.
Named volume `device:` paths live only in the local `docker-compose.yaml`; `docker-compose.defaults.yaml` mounts them by name and must not declare empty volume stubs, because podman-compose cannot merge null stubs with operator dicts.
Every new bind volume or optional knob goes into `docker-compose.yaml.example` too (`hpcperfstats/cursor-rules/docker-compose-settings-example-sync.mdc`), so the next clone gets it.

The services:

| Service    | Image                                                | Role                                             |
| ---------- | ---------------------------------------------------- | ------------------------------------------------ |
| `web`      | `hpcperfstats` (target `hpcperfstats-full`)          | Django under Gunicorn                            |
| `pipeline` | `hpcperfstats` (same image as `web`)                 | supervisord ingest, archive and metrics programs |
| `db`       | `hpcperfstats-db:guard1`                             | TimescaleDB on PostgreSQL 18                     |
| `valkey`   | `docker.io/valkey/valkey:9.2.0-alpine3.24`           | Shared cache                                     |
| `rabbitmq` | `docker.io/library/rabbitmq:4.4.1-management-alpine` | Broker for monitor -> site messages              |
| `proxy`    | `hpcperfstats-proxy`                                 | nginx TLS front door, static files and the SPA   |

## web

`web` builds from `services-conf/pipeline_web.Dockerfile` with context `./` (build target `hpcperfstats-full`, image `hpcperfstats`).
It runs Django under Gunicorn on `0.0.0.0:8000` in the container through `services-conf/django_startup.sh`.
Publishing it on the host is an optional, commented knob in `docker-compose.yaml.example` (`HPCPERFSTATS_WEB_PORT`, default `8000`).

`web` has no `depends_on`: `django_startup.sh` waits for Valkey (`dbload/lib/valkeywait.py`) and PostgreSQL (`dbload/lib/dbwait.py`) before it starts work.
Startup then runs, in order:

1. `manage.py migrate` only.
   Schema changes ship as reviewed, committed migration files; production startup never runs `makemigrations`.
2. `collectstatic --noinput --clear`, which empties disk `STATIC_ROOT` of unused leftovers and repopulates it; it omits `*.map` source maps.
3. A check for the SPA shells under `STATIC_ROOT/frontend/{machine,pub}/index.html`; if the image lacks them, web fail-closes.
   Volume fingerprint heal after a later image rebuild is in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#spa-static-files).
4. Brotli-11 and Gzip-9 sidecars beside compressible static files (hashed Next chunks, Django and DRF admin assets).
5. A publish of `STATIC_ROOT` and `MEDIA_ROOT` onto the shared tmpfs volumes `staticfiles_ram` and `media_ram`, which `proxy` mounts at `/srv/static` and `/srv/media`.

The disk binds `/data/hpcperfstats_site/staticfiles` (`staticfiles_data`) and `/data/hpcperfstats_site/media` (`media_data`) stay the staging copies; empty media is valid.

Browsers must load `/static/*` through `proxy` (ports 80 and 443).
Hitting `web:8000` directly is not a supported way to load hashed SPA assets, because Gunicorn does not serve `/static/` URLs.
For local parity, run the full stack including `proxy`, or run `manage.py runserver --nostatic` and still reach `/static/` through nginx rather than Django's dev static handler.

## pipeline

`pipeline` uses the same image as `web` and runs `supervisor_startup.sh`, which supervises the long-running ingest and processing programs.
It reaches `db`, `valkey` and `rabbitmq` on `hpcperfstats_net`, and `depends_on` `web` only.

### Data volume

The `hpcperfstatsdata` bind (site `device:` `/data/hpcperfstats_data/site_data`) maps to `/hpcperfstats/` in `pipeline` and `web`.
It holds `/hpcperfstats/accounting`, `/hpcperfstats/archive`, `/hpcperfstats/daily_archive` and `/hpcperfstats/logs/` for cluster syslog.
The root `supervisor_startup.sh` must `chown` the data bind and copy SSH keys before supervisord drops privileges, so never add compose `user: "901860:901860"` on `pipeline`.

### Supervisord programs

Tracked `services-conf/supervisord.conf` is baked into the image, with no supervisord `.example` to copy.
supervisord runs as `hpcperfstats`, and its control socket is `unix:///tmp/supervisor.sock` (mode `0700`); do not use `unix:///var/run/supervisor.sock`.
To stop it by hand: `supervisorctl -c /home/hpcperfstats/services-conf/supervisord.conf shutdown`.

| Program                          | Runs                                                         | Start delay | Stop                       |
| -------------------------------- | ------------------------------------------------------------ | ----------- | -------------------------- |
| `hpcperfstats-rabbitmq-listener` | `listend.py`: RabbitMQ consumer, archive append and rotation | None        | `TERM`, `stopwaitsecs=130` |
| `rabbitmq-watcher`               | `rabbitmq_watcher.py`: broker memory poll                    | 30 s        | `KILL`, `stopwaitsecs=1`   |
| `rsync_data`                     | `rsync_data_wrapper.sh`                                      | None        | `KILL`, `stopwaitsecs=1`   |
| `sync_timedb`                    | `sync_timedb.py`: archive -> database ingest                 | 20 s        | `TERM`, `stopwaitsecs=130` |
| `update_metrics`                 | `update_metrics.py`: job metrics and plot artifacts          | 90 s        | `TERM`, `stopwaitsecs=130` |
| `ingest_coverage_sweeper`        | Admin Monitor ingest coverage sweep                          | 120 s       | `TERM`, `stopwaitsecs=70`  |

Every program stops as a group (`stopasgroup=true`, `killasgroup=true`).
`sync_timedb` caps its cooperative drain at `SHUTDOWN_DRAIN_TIMEOUT_S` (120s), so the drain programs get `stopwaitsecs=130`.
A force-kill past `stopwaitsecs` shows as the supervisord line `killing '...' with SIGKILL` on pipeline stdout.
[Pipeline concurrency](../explanation/pipeline-concurrency.md#startup-stagger-and-graceful-stop) explains the stagger and stop order.

### sync_timedb arguments

With no arguments (how supervisord runs it), the queue orchestrator covers the whole archive with hot and catch-up ingest bands.
For a manual run:

- One `YYYY-MM-DD` (that day) or two dates (start and end) set the discover range at entry.
- A leading `once` exits after one idle pass, for example `once 2024-01-15`.
- `--jid <JID>` ingests one job only, with no archive or day-close work.

The words `all`, `backlog` and `current` are rejected.

### rsync

The `rsync_data` program always runs `rsync_data_wrapper.sh`, which prefers `rsync_data.sh` when present, otherwise `rsync_data.sh.example`.
Both scripts start with a guard (`sleep 43200`, `echo "rsync not yet configured"`, `exit`), so a default deploy idles for 12 hours and never connects to remote hosts.

To enable site rsync, edit `rsync_data.sh`: remove those three guard lines, put the rsync commands in the `while true` loop, and put `sleep 43200` at the end of the loop so the script does not tight-loop.
The SSH keys come from the `ssh_keys` volume.

### Memory cap

On hosts with about 192 GiB RAM and no swap, set `mem_limit: 128g` and `memswap_limit: 128g` on `pipeline` in `docker-compose.yaml` (commented in `docker-compose.yaml.example`; not set in the base compose).
Ingest spikes then cgroup-OOM inside the container before they starve `db` or `web`.
Without it the pipeline cgroup is unlimited (`memory.max` is `max`) and `sync_timedb` has no memory budget.
[Pipeline configuration](../reference/pipeline-configuration.md#ingest-memory) lists the `[PIPELINE]` RSS knobs that pair with the cap.

As a second guard, `supervisor_startup.sh` sets the pipeline's `oom_score_adj` to +500, inherited by every daemon, so a host-level OOM kills an ingest daemon before a Postgres backend.
podman-compose ignores a compose `oom_score_adj` key, so it is not in compose.
Recreating after limit changes is in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#change-pipeline-limits).

### Cluster syslog

`pipeline` publishes TCP and UDP port 514 on the host (`HPCPERFSTATS_SYSLOG_PORT`) for sites that enable cluster syslog by hand.
`syslog-ng` and `seal_syslog_daily` are not supervisord programs: the pipeline supervisor runs as `hpcperfstats` (uid 901860) and cannot bind privileged port 514.

Nodes forward syslog to `<PIPELINE_HOST>:514` (rsyslog: TCP `@@host:514`, UDP `@host:514`).
Live files go under `/hpcperfstats/logs/current/` as `$HOST.$R_YEAR$R_MONTH$R_DAY.log`, and `seal_syslog_daily` packs the previous day's files into `/hpcperfstats/logs/log_archive/YYYY-MM-DD-syslog.tar.gz`.

To enable cluster syslog (as root inside `pipeline`), run the two commented lines in `supervisor_startup.sh`, or:

```bash
podman-compose -p hpcperfstats exec pipeline sh -lc '
  mkdir -p /var/lib/hpcperfstats-syslog &&
  /usr/local/bin/python3 -m \
    hpcperfstats.render_syslog_ng_generated &&
  /usr/sbin/syslog-ng -F --no-caps \
    -f /home/hpcperfstats/services-conf/syslog-ng.conf
'
```

`<PIPELINE_HOST>`: the host running `pipeline`.

The render must run before syslog-ng starts: pipeline startup does not refresh `/var/lib/hpcperfstats-syslog/generated.conf`, and the image ships only a build-time fallback fragment.
Changing `[SYSLOG] allow_from` needs a new render and a syslog-ng restart; a pipeline recreate alone does not refresh that fragment.
For daily seals, run `python3 -m hpcperfstats.seal_syslog_daily` as `hpcperfstats` on a schedule (it is not supervised).

syslog-ng emits periodic internal stats (`stats(freq(3600))` in `services-conf/syslog-ng.conf`); `syslog-ng-ctl stats` (as root inside `pipeline`) prints the counters.
Watch disk use on the data volume, because `logs/log_archive` grows with cluster size and retention.

If packets reach the host but nothing is logged, check in this order: syslog-ng is running, firewall rules allow 514, traffic targets the published 514 on the host running `pipeline`, `allow_from` includes the sender's IPv4 address, and forwarders send a sensible hostname or FQDN (it names the file).

### Admin Monitor ingest coverage

The staff Admin Monitor section Ingest vs Job Detail coverage lists the `(type, event)` pairs hosts write to `host_data` that `update_metrics` never reads for Job Detail, and the reverse, grouped by gap set, rack, schema variant or slug.
The `ingest_coverage_sweeper` program scans one host at a time (a 60-minute window ending at each host's newest sample, never older than 7 days) and writes results to Valkey only; the page reads Valkey only.
Its keys are under [Ingest coverage](pipeline-configuration.md#ingest-coverage); on slow-storage sites set `ingest_coverage_sweep_enabled = no` on the same redeploy.

Host status values:

- current: closed segments are in `host_data`; data in the open segment waits for the next monitor `$` rotation, which is normal.
- ingest behind: the same closed file was not ingested on two sweeps.
- unknown: no `listend` rotation record yet.
- incomplete: the scan failed.
- no recent data: no sample in 7 days.

The code that reads each `(type, event)` pair is listed in the [monitor variables consumer map](monitor-variables-consumer-map.md).

### Accounting reimport

Every ingested accounting day stays on disk as `{acct_path}/YYYY-MM-DD.txt`, and no supervisord program runs `hpcperfstats/dbload/sync_acct.py`.
Run it by hand inside `pipeline` to load those files into `job_data` again, for example after restoring an older day:

```bash
podman-compose -p hpcperfstats exec -u hpcperfstats pipeline \
  python3 /home/hpcperfstats/hpcperfstats/dbload/sync_acct.py \
  2024-01-01 2024-01-08
```

Arguments are an optional start and end date (`YYYY-MM-DD`, both inclusive); a missing start defaults to today and a missing end to tomorrow.
Jobs already in `job_data` are skipped.
Add `--dead-letter-unimportable` to move a daily file that cannot be read or imported into `{acct_path}/.unimportable-joblist`; the run ends by printing each moved file and the reason.
Without the flag those files stay in place.

## db

`db` is TimescaleDB on PostgreSQL 18, built from `services-conf/db.Dockerfile` as `hpcperfstats-db:guard1` ([db image](#db-image)); network alias `db`.
Its data lives on the bind `postgres_data_pg18` (`/data/hpcperfstats_db/pg18` in `docker-compose.yaml.example`, owned by uid and gid 70) mounted at `/var/lib/postgresql`, with PGDATA in `18/docker`.
It sets `shm_size: "16gb"`; keep that value with `shared_buffers=64GB`.
The pipeline daemons do not reserve a separate `shm_size`.

### Connections

`web` and `pipeline` reach `db` over its Unix socket at `/run/postgresql` on the shared `postgres_runtime` tmpfs volume (not a host bind), which is the INI default `[DEFAULT] host`.
Socket logins need the `password` (scram-sha-256, as for network logins) through the committed `services-conf/postgres_pg_hba.conf` (`-c hba_file=`).
TCP `5432` stays up on `hpcperfstats_net`; use `host = db` only for a Postgres that does not share the socket volume.

### Server settings

`max_connections` is 500, so overlapping Gunicorn workers, threaded API routes (`job_plots`, `home_options`, `job_detail` aux tasks) and pipeline pools rarely hit `too many clients`.
[Pipeline configuration](pipeline-configuration.md#postgresql-connection-budget) has the slot budget.

| Setting                   | Value                                                                                                                                           |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `max_connections`         | `max_connections=500`                                                                                                                           |
| Parallel workers          | `max_worker_processes=32`, `max_parallel_workers=24`, `max_parallel_workers_per_gather=4`, `max_parallel_maintenance_workers=2`                 |
| Memory                    | `shared_buffers=64GB`, `work_mem=8MB`, `maintenance_work_mem=256MB`, `autovacuum_work_mem`, `temp_buffers`                                      |
| Checkpoints and WAL       | `checkpoint_timeout=30min`, `checkpoint_completion_target`, `min_wal_size=4GB`, `max_wal_size=64GB`, `wal_buffers=128MB`, `wal_compression=lz4` |
| Background writer and I/O | `bgwriter_lru_maxpages=800`, `bgwriter_lru_multiplier=5`, `maintenance_io_concurrency=64`                                                       |
| Session timeouts          | `statement_timeout`, `idle_in_transaction_session_timeout` (same as the Django defaults)                                                        |

A low `work_mem`, small maintenance and autovacuum work mem and `temp_buffers` cap memory spikes; inline comments in the compose file explain each value.
If legitimate bulk jobs slow down, raise `work_mem` only during batch windows or raise the `db` container `mem_limit`, rather than allowing unconstrained per-query memory.
For memory-constrained hosts, start from the conservative values in `hpcperfstats.ini.example` and scale up after checkpoints and container RSS stay stable.

Apply GUC changes with `podman-compose -p hpcperfstats up -d --force-recreate --no-build db`.
PGDATA often sits on a different host mount than the pipeline archive data, so check `df` on both before blaming checkpointer I/O on a full archive volume.

### Compression policy

The `host_data` compression policy (`compress_after = 8 days`) runs on a daily fixed schedule at 01:00 in the INI `timezone`, pinned by migration `0040`, with no `max_runtime` (a run that overruns finishes its chunk).
The multi-hour chunk rewrite then does not compete with daytime ingest for WAL.
The migration is a bounded `alter_job` (no compress or decompress) and soft-skips when Timescale or the policy is absent.
The policy restore in migration `0032` (`add_compression_policy`) re-pins the same schedule through the shared `compression_schedule` helper, so a re-added policy does not fall back to the floating default.

## db image

`db` runs the homemade `hpcperfstats-db:guard1` image built from `services-conf/db.Dockerfile` (PostgreSQL 18 + TimescaleDB, WAL compression through `/opt/lz4`).
`./scripts/rebuild_full_site.sh` builds it with Clang ThinLTO through `services-conf/bake_clang_flags.sh`; `pull_policy: never`, so a missing tag fails instead of pulling.

| Item                     | Contract                                                                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Base                     | `alpine:3.24.x` pinned by `ARG ALPINE_VERSION` (never `latest`)                                                                                      |
| PostgreSQL and Timescale | SHA-pinned `ARG PG_VERSION` and `ARG TIMESCALEDB_VERSION`; Timescale built without `APACHE_ONLY`                                                     |
| `/opt` libraries         | Source-built jemalloc, zlib-ng, lz4 and zstd, with an rpath on `postgres` and the zstd CLI; PostgreSQL configured `--without-icu --without-liburing` |
| Toolchain                | Clang 22 ThinLTO with `-march=native`; bake on the production CPU                                                                                    |
| Volume                   | `/var/lib/postgresql` (PG18 layout)                                                                                                                  |
| Runtime settings         | The `db` server settings above, plus `io_method=worker` and `io_workers=8`                                                                           |

The Timescale catalog updates itself.
On every start of an existing data directory, `services-conf/db-docker-entrypoint.sh` runs `ALTER EXTENSION timescaledb UPDATE` in each database whose catalog is older than the image, on a socket-only temporary server with the Timescale background workers off; the healthcheck stays red until it finishes.
Bumping `ARG TIMESCALEDB_VERSION` therefore means rebuilding and recreating `db`, with no manual `ALTER EXTENSION`.

To read the result:

```bash
podman-compose -p hpcperfstats logs db 2>&1 \
  | grep 'timescaledb auto-update'
```

A `FAILED` line (no update path in the image) or a `newer than image` line (image rolled back) leaves the catalog alone, and Postgres still starts; fix it by hand.
After an unclean stop, crash recovery runs inside that temporary server (`redo in progress` in the log, TCP `Connection refused`) for as long as it needs; do not restart the container meanwhile.

### Data-dir guard

Before it writes anything in the bind, the entrypoint refuses a `PG_VERSION` other than 18 at the bind root, under `18/` or at `data/`.
With `HPCPERFSTATS_DB_ROLE=primary` it writes `hpcperfstats-primary` in the bind root before `initdb` on an empty bind, and refuses an existing cluster without that marker or an empty bind that carries `hpcperfstats-migrate-target`.
It then holds `flock` on `.hpc-server.lock` in the bind root for the life of the server, so a second server on the same folder refuses to start.
A refusal prints `hpcperfstats db guard: refusing to start`, the path and the reason, and exits before any `chown`, `chmod` or `mkdir`.
The lock file must be creatable by root in the bind root; NFS `root_squash` binds are not supported.

## Migration services

A site that still runs PostgreSQL 15 lists `docker-compose.pg15-primary.yaml` after the defaults in its `include:`, and its `docker-compose.yaml` declares both `postgres_data` (PG15) and `postgres_data_pg18`:

- `db` stays Hub `docker.io/timescale/timescaledb:2.28.3-pg15` under the same name, alias and socket, with the PostgreSQL 15 server settings, entrypoint `services-conf/db-docker-pg15-guard.sh` and `HPCPERFSTATS_DB_ROLE=pg15-legacy`.
  The guard refuses anything but a PG15 cluster, any PG18 file and `hpcperfstats-retired`, and holds the same lock in the PG15 bind.
- `db_pg18` is the PG18 copy target: profile `pg18-migrate`, alias `db18`, `HPCPERFSTATS_DB_ROLE=migrate-target`, the `db` server settings and no socket.

[Migrate to PostgreSQL 18](../how-to/operate/migrate-to-postgresql-18.md) moves such a site to PostgreSQL 18; cutover deletes the include line.

## valkey

`valkey` is a dedicated container (`docker.io/valkey/valkey:9.2.0-alpine3.24`, Valkey 9.2), run as `valkey-server` so the image entrypoint drops root; network alias `valkey`.
It is the shared instance for the Django cache (TTL keys), the listend auxiliaries and the ingest coverage results ([Valkey roles](../explanation/architecture.md#valkey-roles)).

| Setting     | Value                                                                                                                                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Memory cap  | `maxmemory 16gb`                                                                                                                                                                                        |
| Eviction    | `volatile-lru`: Django cache keys keep a TTL and stay evictable                                                                                                                                         |
| Threads     | `--io-threads 4`                                                                                                                                                                                        |
| Persistence | None (`appendonly no`)                                                                                                                                                                                  |
| Socket      | `/run/valkey/valkey.sock` (`--unixsocketperm 777`) on the `valkey_runtime` named volume, shared with `web` and `pipeline` (not a host bind); `services-conf/valkey_entrypoint.sh` creates `/run/valkey` |
| TCP         | `6379`, for `valkey-cli` and external Valkey URLs                                                                                                                                                       |
| Healthcheck | TCP `PING` and socket `PING`, so `ps` shows health                                                                                                                                                      |

Never use an `allkeys-*` policy: it would evict Django and `listend` keys that carry no TTL.
Size the host so Valkey can use its cap alongside Postgres `shm_size` and `shared_buffers`.

Do not put Valkey on `web` or `pipeline` `depends_on` with any `condition:`, including `service_started` and `service_healthy`.
podman-compose can then create `hpcperfstats_valkey_1` and never start it, so `logs valkey` stays empty.
The startup wait reads `[CACHE] valkey_location`, remaps the Compose hostname `valkey` (including a baked `valkey://valkey:6379/1`) to `unix:///run/valkey/valkey.sock?db=1`, and falls back to that socket URL when the INI key is missing.

Valkey holds no durable user data: flushing it is safe after a cold start, at the cost of cache misses and plot rebuilds.
Valkey image bumps on an existing host are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#bump-the-valkey-image).

## rabbitmq

`rabbitmq` is the broker for monitor -> site delivery (`docker.io/library/rabbitmq:4.4.1-management-alpine`).
It publishes AMQP 5672 (image `rabbitmq:*-management-alpine`); the management HTTP API on `http://rabbitmq:15672` stays compose-internal (port 15672 is not published on the host).

### Memory cap

RabbitMQ's default `vm_memory_high_watermark` is 40% of detected host RAM, which can OOM the box under thousands of monitor publishers.
Compose sets `mem_limit: 96g` and `memswap_limit: 96g`, and mounts `services-conf/rabbitmq_vm_memory.conf` (`vm_memory_high_watermark.absolute = 64GiB`).
Publishers block about 32 GiB below the cgroup hard wall, before Erlang `binary_alloc` fails at the limit.
On hosts with less than 96 GiB RAM, lower `mem_limit`, `memswap_limit` and the absolute watermark together, then recreate `rabbitmq` as in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#recreate-the-broker-after-a-config-change).

### Broker settings

| Setting            | Value                                                                                                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Erlang allocator   | `ERL_FLAGS=+MBas aobf +MBlmbcs 512 +MHlmbcs 512`: address-order best-fit binary allocator and 512 KiB largest multiblock carriers, against fragmentation under many publishers |
| Logging            | Console `warning` (not `info`); connection and channel at `error` (`services-conf/rabbitmq_logging.conf`)                                                                      |
| Crash dumps        | Off: `ERL_CRASH_DUMP_SECONDS=0`, `ulimits.core: 0`                                                                                                                             |
| Default queue type | `services-conf/rabbitmq_default_queue_type.conf` (`default_queue_type = quorum`); new durable monitor ingest queues are quorum                                                 |
| Frame size         | `services-conf/rabbitmq_frame_max.conf` (`frame_max = 131072`, the RabbitMQ default), matching pika `LISTEND_AMQP_FRAME_MAX`                                                   |
| Node name          | Static `hostname: rabbitmq-prod` and `RABBITMQ_NODENAME=rabbit@rabbitmq-prod`, so recreates add no `rabbit@*` mnesia trees                                                     |
| Management login   | `loopback_users.guest = false` in `services-conf/rabbitmq_management.conf`, so `web` can authenticate to `rabbitmq`                                                            |

`stop_grace_period: 30m` gives the broker time for orderly shutdown (quorum and mnesia) before SIGKILL.
Recreate `rabbitmq` after changing limits, the watermark, logging, crash-dump knobs or `ERL_FLAGS`; recreate `rabbitmq` and `pipeline` after changing `rabbitmq_frame_max.conf` ([Upgrade the stack](../how-to/operate/upgrade-the-stack.md#recreate-the-broker-after-a-config-change)).
Existing brokers and the quorum default are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#recreate-the-broker-after-a-config-change); recovery and preserve-extract are in [Recover RabbitMQ](../how-to/operate/recover-rabbitmq.md).

### Broker monitoring

The staff Admin Monitor RabbitMQ statistics page uses the management HTTP API from `web`, not AMQP.

The pipeline supervisord program `rabbitmq-watcher` polls the management API every 5 min and logs lines that start with `[rabbitmq-watcher]`.
It logs a literal `ERROR` at 40 GiB and every +10 GiB above that.

To inspect the broker, use `rabbitmqctl status` (its Alarms section and the watermark).
Do not use `rabbitmqctl list_alarms`: it is absent on RabbitMQ 4.4.1, the pinned image.

## proxy

`proxy` is the nginx TLS front door, image `hpcperfstats-proxy`, source-built from `services-conf/proxy.Dockerfile`.
It serves static files, media and the SPA export from the `staticfiles_ram` and `media_ram` volumes and proxies API and HTML requests to `web`.

### Image

The image pins nginx 1.31.10, PCRE2 10.49 under `/opt/pcre2` (shared + JIT; no apk `pcre2`), jemalloc, zlib-ng (`ZLIB_COMPAT`), OpenSSL 3.5.x, `ngx_brotli`, and zstd 1.6.0 (static `libzstd.a` with the GetPageSpeed zstd-nginx-module).
Every source-built library bakes with `-march=native -mtune=native` ThinLTO through `bake_clang_flags.sh`, including OpenSSL `--with-openssl-opt`; the runtime keeps `libstdc++` for brotli.
Native flags are not portable across CPU generations, so build the proxy image on the production host; do not rely on Alpine edge apk nginx packages.

At build time the image `COPY`s `nginx-main.conf`, `cp`s `nginx.conf` into `default.conf` for a non-Compose baseline, generates `hps-proxy-allowed-hosts.inc` from `[DEFAULT] server=`, and ships `proxy_entrypoint.sh` with the TLS and resolver helpers.

### Mounts

Compose replaces the baked configs with host mounts:

- `./services-conf/nginx-main.conf` at `/etc/nginx/nginx.conf`: process and http tunables (`worker_processes auto`, affinity, `sendfile`/`tcp_*`, `open_file_cache`).
- `./services-conf/nginx.conf` at `/etc/nginx/http.d/default.conf`.
- The shared snippets `nginx-static-files.conf`, `nginx-api-route-allowlist.inc`, `nginx-django-proxy-common.inc`, `nginx-compress-proxy.inc`, `nginx-compress-static.inc`, `nginx-edge-security-headers.inc`, `nginx-csp-no-active.inc` and `nginx-csp-django-html.inc`, the only runtime source for those snippets (they are not baked into `proxy.Dockerfile`).
- `proxy_ssl_source:/mnt/ssl-source:ro`, the TLS settings volume.

### TLS

`services-conf/nginx.conf` uses fixed in-container TLS paths, `/etc/ssl/hpcperfstats/fullchain.pem` and `privkey.pem`; do not edit TLS paths in nginx.
TLS PEMs are not baked into the image: at container start `proxy_entrypoint.sh` runs `resolve_proxy_ssl_certs_dir.py`, which materializes real PEM files from `/mnt/ssl-source` into `/etc/ssl/hpcperfstats` before nginx starts.
The stack has no Compose `ssl_certs` volume, no build-time host `/` bind, no `additional_contexts` or manual resolve step, and no production `.env`.
Set `proxy_ssl_source.device` in `docker-compose.yaml` and, for Let's Encrypt, the optional `HPCPERFSTATS_SSL_CERTS_REL`.
Never point production `proxy_ssl_source.device` at `tests/fixtures/proxy-ssl`.

After a certificate renew or a change of TLS source path, restart `proxy` only (`podman-compose -p hpcperfstats restart proxy`); no image rebuild is needed.
Image rebuilds that leave `proxy` running are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#choose-a-rebuild-script).

The entrypoint also regenerates the OCSP `resolver` include from the container `/etc/resolv.conf`, waits for SPA HTML under `/srv/static/frontend/{machine,pub}/index.html`, and may write private diagnostic CSP includes under `/etc/nginx/` (never under `/srv/static`).
Certificates without an AIA OCSP URL do not staple, which must not take the site offline.

### Hostnames

Hostnames come from `[DEFAULT] server=` in `hpcperfstats.ini` (in the build context, else `hpcperfstats.ini.example`).
`parse_hpcperfstats_proxy_hosts.py` emits `/etc/nginx/hps-proxy-allowed-hosts.inc`, which the main config `include`s for `server_name`.
Requests whose `Host` header does not match get 404 on port 80; on port 443 unknown names get a TLS handshake rejection (`ssl_reject_handshake`).
Restart `proxy` after changing `server=`, the TLS PEMs or `nginx.conf`.
No `docker-compose.yaml` edit is needed for TLS paths or hostnames.

### Routing

`services-conf/nginx-static-files.conf` holds the routing.
nginx serves `/static/` and `/media/` directly, shells the SPA under `/machine/` and `/pub/`, and proxies only an explicit list of Django routes.
Each route is one exact (`location = /login/`) or anchored-regex location, never a `^~` prefix, so probes such as `/login/index.php` 404 at the edge.
A server-level guard also 404s raw URIs that are not normalized (`//`, dot segments, `\`, `;`, encoded `. / \` or control bytes), because nginx matches the normalized path but forwards the raw one.
Shared `proxy_*` directives are in `services-conf/nginx-django-proxy-common.inc`.
Nested `/api/` locations come from the generated `services-conf/nginx-api-route-allowlist.inc`, regenerated from `EXPECTED_ROUTE_TEMPLATES` by `write_nginx_api_route_allowlist_include.py`; unknown `/api/...` paths return 404 at the edge, and every other path gets 404 from nginx.

When you add a top-level Django route, extend the allowlist in `nginx-static-files.conf`.
When you add nested `api/` routes, update the route catalog and regenerate `nginx-api-route-allowlist.inc` in the same change.

### Static files, CSP and compression

HTTP GETs for `*.inc` under `/static/`, and direct `*.br` / `*.gz` / `*.zst` sidecar URLs, return 404.
nginx compresses Gunicorn and SPA HTML on the fly (zstd, then Brotli, then gzip) and serves precompressed `.br`/`.gz` siblings for hashed `/static/frontend/_next/` files with a 1y cache; unhashed `/static/` stays 30d.

nginx is the public authority for HSTS, framing, COOP, Permissions-Policy, Referrer-Policy and CSP (hash-based for SPA shells, no-active for JSON and redirects).
SPA shells carry their own hash CSP in an HTML `<meta http-equiv="Content-Security-Policy">` tag, written at frontend export and SPA heal.
The `/machine/` and `/pub/` locations must not send a competing hash CSP header, and they set `open_file_cache off` so a heal is not served stale.

## Stop grace periods

Compose `stop_grace_period` per service:

| Service    | Default                                   | Why                                                      |
| ---------- | ----------------------------------------- | -------------------------------------------------------- |
| `web`      | 30s (`HPCPERFSTATS_WEB_STOP_GRACE`)       | Gunicorn drain                                           |
| `pipeline` | 480s (`HPCPERFSTATS_PIPELINE_STOP_GRACE`) | Sequential program drains can finish                     |
| `db`       | 30m                                       | SIGINT fast shutdown plus checkpoint on a large database |
| `rabbitmq` | 30m                                       | Quorum and mnesia shutdown                               |

`db` gets 30m before SIGKILL, and so do `rabbitmq` and, on a PostgreSQL 15 site, `db_pg18`.
A shorter pipeline override SIGKILLs mid-drain.
`scripts/rebuild_pipeline.sh` stop timeouts match: `HPCPERFSTATS_PIPELINE_STOP_TIMEOUT` defaults to 480 and `HPCPERFSTATS_WEB_STOP_TIMEOUT` stays 30.
SIGTERM-driven exit 143 is the T0 expectation in [Verify sync_timedb after a deploy](../how-to/operate/verify-sync-timedb.md#run-t0) when the wall clock covers the drain.

## Logging

`docker-compose.yaml` uses the `json-file` logging driver with `max-size: 100m` and `max-file: 3`, so stdout stays available to Compose and does not flood host syslog or journald.

## Development launch overrides

Rootless development uses unprivileged host ports without changing production defaults:

```bash
HPCPERFSTATS_HTTP_PORT=8080 HPCPERFSTATS_HTTPS_PORT=8443 \
  HPCPERFSTATS_SYSLOG_PORT=1514 \
  HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-dev \
  ./scripts/rebuild_full_site.sh
```

These variables are a development launch override only.
Production site configuration stays in `hpcperfstats.ini` and `docker-compose.yaml`, never a required `.env`.

## Rebuild scripts

`./scripts/rebuild_full_site.sh` builds `web`, `proxy` and `hpcperfstats-db:guard1` with `podman build` (Clang ThinLTO through `services-conf/bake_clang_flags.sh` on Alpine `db` and `proxy` and Debian `python-build`), then runs `podman-compose down` and `up -d --no-build`.
Before it builds or stops anything, it refuses when the database binds do not match the site file (a PG18 bind without the marker on a site without the legacy include, a PG15 bind not retired, a site-file `db_pg18:` service).
After `down` it stops every container on the host that still mounts a database bind (`podman stop -t 1800`) and requires both locks free before `up`; on a PostgreSQL 15 site it restarts `db_pg18` only when it was running.
CPython always bakes with upstream `--enable-optimizations` (no custom Clang profile-guided rebuild flags).
The per-service rebuild scripts are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#choose-a-rebuild-script); never use `up --build` for a refresh.

## Common commands

Common operator commands:

| Task                       | Command                                                                                        |
| -------------------------- | ---------------------------------------------------------------------------------------------- |
| Build and start the stack  | `./scripts/rebuild_full_site.sh`                                                               |
| Stop and remove containers | `podman-compose -p hpcperfstats --profile pg18-migrate down`                                   |
| Restart the proxy          | `podman-compose -p hpcperfstats restart proxy`                                                 |
| View logs                  | `podman-compose -p hpcperfstats logs`                                                          |
| PostgreSQL shell           | `podman-compose -p hpcperfstats exec db psql -h localhost -U hpcperfstats`                     |
| Pipeline shell             | `podman-compose -p hpcperfstats exec -u hpcperfstats pipeline bash`                            |
| Queues and message counts  | `podman-compose -p hpcperfstats exec rabbitmq rabbitmqctl list_queues name messages consumers` |
| Broker status and alarms   | `podman-compose -p hpcperfstats exec rabbitmq rabbitmqctl status`                              |

The `--profile pg18-migrate` flag on `down` keeps a PostgreSQL 15 site's `db_pg18` in the graceful stop; without it `db_pg18` is SIGKILLed mid shutdown checkpoint.
Keep `-h localhost` on `psql`: without it `psql` uses the Unix socket, which asks for the password.

To grep the pipeline log for one program:

```bash
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep '\[rabbitmq-watcher\]'
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep 'ingest_coverage sweep'
```

## Pipeline diagnostics

These commands read state; none of them changes the stack except `invalidate_archive_members.py`.

### Memory and OOM

cgroup OOM kills often leave host `dmesg` empty.
To read the pipeline cgroup counters:

```bash
podman-compose -p hpcperfstats exec pipeline sh -c '
  cd /sys/fs/cgroup || exit 1
  echo "=== memory.events ==="
  cat memory.events
  echo "=== memory.current / max / peak ==="
  for f in memory.current memory.max memory.peak; do
    printf "%s: " "$f"; cat "$f" 2>/dev/null || echo n/a
  done
'
```

| `memory.events` field    | Meaning                                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `oom_kill`               | Processes SIGKILLed by the container memory cgroup (non-zero means a cgroup OOM in this container's lifetime) |
| `oom` / `oom_group_kill` | Related cgroup OOM counters                                                                                   |

`podman inspect <PIPELINE_CONTAINER> --format '{{.State.OOMKilled}}'` reports the main PID only.
`memory.events` counters reset when the container is recreated, so for a past incident compare `StartedAt` and `podman events`.
For a full packet, also read `memory.peak`, run `ps -eo pid,rss:10,cmd` to compare the `codon_process_worker` processes with the `sync_timedb.py` main process, `find` the largest raw stats paths, and log the result under `test_runs/`.
Grep kernel logs for `oom-kill` with `sync_timedb.py` and correlate with the `queue_orchestrator` lines in the application log.

`<PIPELINE_CONTAINER>`: the pipeline container name, `hpcperfstats_pipeline_1` by default.

### Archive members store

To force cold members-store reassessment after a crash, wipe `{archive_dir}/.sync_timedb_archive_members/{day}.json` with the host CLI; it never touches the job-store sidecar `.sync_timedb_job_store.json`, and it restarts `pipeline` so worker L1 is cold.
It needs Python 3.14+ (the project venv) if host `python3` is older.

```bash
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --day YYYY-MM-DD --dry-run --archive-dir <ARCHIVE_DIR>
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --day YYYY-MM-DD --archive-dir <ARCHIVE_DIR>
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --all --yes --archive-dir <ARCHIVE_DIR>
```

`<ARCHIVE_DIR>`: `[PIPELINE] archive_dir` as resolved from the INI.

The first form is a dry run (no sidecar unlink, no restart); the second invalidates one day and restarts `pipeline`; the third needs `--yes` for all days, and `--no-restart` leaves pipeline L1 warm.
`sync-timedb-ingest-pool-io-coordination.mdc` item 32 has the contract.

### populate-pool after a deploy

To check the members-store populate workers:

```bash
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E 'populate-pool|chunk prewarm' | tail -40
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E 'member stream failed|ingest per-file timeout' \
  | tail -40
```

Expect `sync_timedb:worker:populate-pool` with `populate_source=tar` when a sibling `.tar` exists (active or closed days), and `populate_source=sealed` only when the tar was dropped.
Expect no `ingest per-file timeout` from `ingest-pool` workers while the members-store populate lock is held and progressing.
`DB lock wait ... batch` must not appear.
Outcome lines include `parse_elapsed_s=` / `postgres_s=` / `elapsed_s=`.

### day_close progress

Grep the pipeline log for `queue_orchestrator day_close claim`, `queue_orchestrator day_close stage_enter`, `queue_orchestrator day_close stage_exit` and `queue_orchestrator day_close vacate` (`outcome=`).

### Connections and ingest rate

- `python hpcperfstats/site/manage.py pg_connection_stats`, run from the repo root with `HPCPERFSTATS_INI` and the database reachable, prints `pg_stat_activity` totals for the current database.
- `scripts/measure_pipeline_ingest_rate.py` (`sync_full_ingest_mib_per_min`) checks the ingest rate with no telemetry.
  With `[PIPELINE] sync_ingest_telemetry = yes`, `scripts/measure_pipeline_ingest_rate.py --log-file ... --since-minutes 480` reads the decision tokens and prints `decision_next=`.

## Related

- [Install HPCPerfStats](../tutorials/install-hpcperfstats.md)
- [Pipeline configuration](pipeline-configuration.md)
- [Pipeline concurrency](../explanation/pipeline-concurrency.md)
- [Architecture](../explanation/architecture.md)
- [Upgrade the stack](../how-to/operate/upgrade-the-stack.md)
