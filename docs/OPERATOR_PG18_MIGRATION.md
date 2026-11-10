# Operator guide: PostgreSQL 15 → homemade PG18 + Timescale (logical migrate)

**Audience:** site operators moving from Hub `timescale/timescaledb:2.28.3-pg15` (service `db`) to the homemade `hpcperfstats-db` image (PostgreSQL 18 + TimescaleDB 2.31.x on Alpine musl; service `db_pg18`).

**Do not use `pg_upgrade`.** Timescale 2.29+ drops PG15 and musl/glibc collations differ. PG18 gets a fresh Django-migrated schema, then rows are copied with binary `COPY`. Never restore `_timescaledb_catalog` from PG15.

Contract rule: `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`.

Run every block as root from the checkout that contains `docker-compose.yaml`. Use **`podman-compose -p hpcperfstats …` without `-f`** throughout.

## Timeline and downtime

| Phase | Stack state | Cost |
|-------|-------------|------|
| 0 Preflight, A build/start, B schema | Live on PG15 | Minutes (A: image bake) |
| C Bulk copy (`host_data`) | Live on PG15 | Hours–days; rerun until clean |
| D1 Catch-up pass | Live on PG15 | Minutes |
| **D2–D3** Pipeline stopped, freeze pass | **Ingest paused** (RabbitMQ buffers monitor data) | Changed slices only |
| **D4** Web + proxy stopped, relational copy | **Site down** | Parallel dump/restore of relational tables |
| **E** Cutover | **Site down** until E3 | Container restarts |

How the copy keeps the window short (`scripts/pg18_host_data_chunk_copy.py`):

- **State file.** Every verified chunk records `{rows, sig}`. The signature (`sig`) is the chunk's PG15 `pg_stat` insert, update and delete counters. With `--changed-only`, chunks whose signature hasn't moved are skipped without a single query.
- **Slice resync.** A changed chunk that has only *gained* rows since it was last verified is compared in 60-minute slices. Only the mismatched slices are deleted and re-copied, so the hot chunk costs minutes of data, not a full day. Any UPDATE or DELETE (operator repair tools) forces a whole-chunk copy.
- **Compression during the copy.** Copied chunks older than 8 days are compressed on PG18 right away, so PG18 never holds the full history uncompressed and there is no compression backlog after cutover.
- **Binary COPY.** It uses an explicit, type-checked column list and fails closed if the PG15 and PG18 `host_data` layouts differ.
- **`--verify` (freeze only).** Each copied chunk is recounted on both sides, and the run exits non-zero on any mismatch. The `verified_rows=` total replaces a full `count(*)` of `host_data`.
- **Per-chunk failures.** A failed chunk is logged and dropped from state; the run continues and exits 1. Rerun the same command.

Freeze precondition: **no `host_data` UPDATE/DELETE tools** (`repair_jstamp_deltas`, `backfill_host_data_null_dev.sh`, `collapse_percore_parallel.sh`) may run on PG15 between D1 and D3. If they do, the affected chunks fall back to whole-chunk copies (correct, just slower).

---

## Phase 0 — host preflight (read-only; paste output)

PG18 compresses as it copies, so its steady-state size ≈ PG15 `db_size` (plus in-flight uncompressed chunks and up to `max_wal_size=64GB` WAL). **Fail closed:** free space on the `pg18` filesystem ≥ **1.2 × PG15 db_size + 80 GB**; `kernel.io_uring_disabled` prints `1` with `io_uring_group = 70` (or `disabled = 0`); the `postgres_data_pg18` bind device exists in `docker-compose.yaml`.

Both servers set `shared_buffers=64GB`. Bulk `COPY`, counts and compression go through PostgreSQL's small ring buffers, so PG18 does not fill its 64 GB during dual-run.

```bash
hostname; git log -1 --oneline; free -g | head -2
grep -n -A5 -E '^  postgres_data(_pg18)?:' docker-compose.yaml | grep -E 'postgres_data|device'
df -h /data/hpcperfstats_db 2>&1 | tail -1
sysctl kernel.io_uring_disabled kernel.io_uring_group
podman-compose -p hpcperfstats exec -T db psql -h localhost -U hpcperfstats -d hpcperfstats -X -c "SET statement_timeout = 0" -c "SELECT pg_size_pretty(pg_database_size(current_database())) AS pg15_db_size" -c "SELECT pg_size_pretty(sum(before_compression_total_bytes)) AS before, pg_size_pretty(sum(after_compression_total_bytes)) AS after FROM hypertable_compression_stats('host_data')" -c "SELECT count(*) AS chunks, count(*) FILTER (WHERE is_compressed) AS compressed, min(range_start)::date AS oldest, max(range_end)::date AS newest FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data'" -c "SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'host_data' ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 8"
```

---

## Phase A — host prerequisites, build and start PG18 beside PG15

Hub `db` (alias `db`) stays the live writer; PG18 is profile `pg18-migrate`, alias `db18`, bind `postgres_data_pg18` (Alpine postgres **uid/gid 70**: a bind replaces image ownership, so `root:root` gives `mkdir … /18/: Permission denied`). io_uring: `kernel.io_uring_disabled=1` + `kernel.io_uring_group=70` (with group unset, uid 70 gets EPERM even under `seccomp=unconfined`); **forbidden** `disabled=2`. Bake on the production CPU (`-march=native`).

```bash
mkdir -p /data/hpcperfstats_db/pg18 && chown -R 70:70 /data/hpcperfstats_db/pg18 && ls -ld /data/hpcperfstats_db/pg18
printf '%s\n' 'kernel.io_uring_disabled = 1' 'kernel.io_uring_group = 70' > /etc/sysctl.d/99-hpcperfstats-io-uring.conf && sysctl -p /etc/sysctl.d/99-hpcperfstats-io-uring.conf
podman build -f services-conf/db.Dockerfile -t hpcperfstats-db services-conf && podman-compose -p hpcperfstats --profile pg18-migrate up -d --no-build db_pg18
for i in $(seq 1 60); do podman-compose -p hpcperfstats --profile pg18-migrate exec -T db_pg18 pg_isready -h 127.0.0.1 -U hpcperfstats -d postgres -q && break; sleep 5; done
podman-compose -p hpcperfstats --profile pg18-migrate exec -T db_pg18 sh -c 'psql -h localhost -U hpcperfstats -d postgres -XtAc "SELECT 1 FROM pg_database WHERE datname = '\''hpcperfstats'\''" | grep -q 1 || psql -h localhost -U hpcperfstats -d postgres -X -v ON_ERROR_STOP=1 -c "CREATE DATABASE hpcperfstats OWNER hpcperfstats"; psql -h localhost -U hpcperfstats -d hpcperfstats -X -c "SELECT version()" -c "SHOW io_method" -c "SHOW shared_buffers" -c "SHOW hba_file" -c "SELECT default_version FROM pg_available_extensions WHERE name = '\''timescaledb'\''"'
```

**Fail closed:** `ls -ld` shows owner `70 70`; `version()` is PostgreSQL 18; `io_method` is `io_uring` (otherwise re-check sysctl, then `podman-compose -p hpcperfstats --profile pg18-migrate up -d --force-recreate db_pg18`); `hba_file` is `/etc/postgresql/pg_hba.conf`. The database create step is idempotent; init can be skipped after an early crash loop.

---

## Phase B — empty schema on PG18 (Django migrate)

The deployed `web` image must carry the current migrations (`machine.0001_initial` creates the timescaledb extension before `create_hypertable`). The long-running `web` keeps using PG15. This one-shot points Django at `db18`, migrates, verifies an empty `host_data` hypertable, and removes the compression policy so the copy script owns compression until cutover.

```bash
podman-compose -p hpcperfstats --profile pg18-migrate run --rm --no-deps --entrypoint /usr/local/bin/python3 web -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hpcperfstats.site.hpcperfstats_site.settings")
import django
django.setup()
from django.conf import settings
from django.db import connection, connections
connections.close_all()
settings.DATABASES["default"]["HOST"] = "db18"
from django.core.management import call_command
call_command("migrate", verbosity=1)
with connection.cursor() as cur:
    cur.execute("SELECT version()")
    print("server", cur.fetchone()[0])
print("migrate_ok")
'
podman-compose -p hpcperfstats --profile pg18-migrate exec -T db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -XtA -v ON_ERROR_STOP=1 -c "SELECT 'host_data_n=' || count(*) FROM host_data" -c "SELECT 'compression_enabled=' || compression_enabled FROM timescaledb_information.hypertables WHERE hypertable_name = 'host_data'" -c "SELECT remove_compression_policy('host_data', if_exists => true)" -c "SELECT 'compression_jobs=' || count(*) FROM timescaledb_information.jobs WHERE hypertable_name = 'host_data' AND proc_name LIKE '%compression%'"
```

Checks run in `psql`, not through Django's cursor: on hpcperfstats02 (2026-11-06) the `web` image rejected parameterized `cursor.execute(sql, params)` with `the query has 1 placeholders but 2 parameters were passed`. Rerunning this block is safe (`migrate` is idempotent; the policy removal uses `if_exists`).

**Fail closed:** `server` is PostgreSQL 18, `migrate_ok`, `host_data_n=0`, `compression_enabled=true`, `compression_jobs=0`. If you see `create_hypertable … does not exist`, the `web` image predates the `0001` extension fix; redeploy, then rerun.

---

## Phase C — bulk `host_data` copy (live, rerunnable)

The copy runs detached in a one-shot `web` container using the checkout's script. State persists in `/hpcperfstats/pg18_migrate/state.json` (the `hpcperfstatsdata` bind), so reruns resume. `--watermark-days -7` selects every chunk including today's, and slice resync makes repeat passes over hot chunks cheap. `--workers 4` is the starting point; lower it if PG15 ingest latency suffers.

### web — start the copy (paste output)

```bash
podman rm -f hpcperfstats_pg18_copy 2>/dev/null; podman-compose -p hpcperfstats --profile pg18-migrate run -d --no-deps --name hpcperfstats_pg18_copy -e PGPASSWORD=hpcperfstats -v "$PWD/scripts:/migrate:ro" --entrypoint /usr/local/bin/python3 web -u /migrate/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --watermark-days -7 --changed-only --state-file /hpcperfstats/pg18_migrate/state.json --workers 4
```

### host — progress, then result when it exits (paste output)

```bash
podman logs hpcperfstats_pg18_copy 2>&1 | grep -c -E 'INFO (copy|skip) chunk'; podman logs hpcperfstats_pg18_copy 2>&1 | grep -E 'source_chunks=|ERROR|INFO done' | tail -5; podman inspect hpcperfstats_pg18_copy --format 'status={{.State.Status}} exit={{.State.ExitCode}}'
```

**Done when** `status=exited exit=0` and the `INFO done` line shows `failed=0`. On `exit=1`, read the `ERROR chunk failed` lines and rerun the start block; failed chunks are retried and verified chunks are skipped. Rerun the start block a few times as convenient: each pass only touches chunks written since the last.

---

## Phase D — freeze (downtime) and relational copy

Schedule D1–E back to back.

### D1 + D2 — catch-up pass while live, then stop the pipeline (paste output)

```bash
podman rm -f hpcperfstats_pg18_copy 2>/dev/null; podman-compose -p hpcperfstats --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats -v "$PWD/scripts:/migrate:ro" --entrypoint /usr/local/bin/python3 web -u /migrate/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --watermark-days -7 --changed-only --state-file /hpcperfstats/pg18_migrate/state.json --workers 4 2>&1 | grep -E 'ERROR|INFO done'
podman-compose -p hpcperfstats stop pipeline; podman-compose -p hpcperfstats ps pipeline
podman-compose -p hpcperfstats exec -T db psql -h localhost -U hpcperfstats -d hpcperfstats -X -c "SELECT pid, application_name, client_addr, state, left(query, 80) AS query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND (query ILIKE '%host_data%' OR state <> 'idle') ORDER BY pid"
```

**Fail closed:** the catch-up shows `failed=0`; pipeline is stopped; no `INSERT`/`COPY` into `host_data` remains in `pg_stat_activity` (web read queries are fine).

### D3 — freeze pass with verification (paste output)

```bash
podman-compose -p hpcperfstats --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats -v "$PWD/scripts:/migrate:ro" --entrypoint /usr/local/bin/python3 web -u /migrate/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --watermark-days -7 --changed-only --verify --state-file /hpcperfstats/pg18_migrate/state.json --workers 4 2>&1 | grep -E 'slice resync|ERROR|INFO done'; echo "freeze_exit=${PIPESTATUS[0]}"
```

**Fail closed:** `freeze_exit=0` and `failed=0`. Record `verified_rows=` for the change log. On failure, rerun D3 (only the failed chunks are examined).

### D4 — stop web + proxy, copy relational tables (paste output)

`scripts/pg18_relational_copy.sh` runs inside `db_pg18` and fails closed. It checks that both sides have the same table set, then:

1. parallel `pg_dump -Fd` from `db`;
2. `TRUNCATE … RESTART IDENTITY CASCADE` on PG18;
3. parallel `pg_restore --data-only --disable-triggers`;
4. exact per-table row counts (must match), then `ANALYZE`.

`host_data` and `django_migrations` are excluded.

```bash
podman-compose -p hpcperfstats stop web proxy; podman-compose -p hpcperfstats ps web proxy
podman-compose -p hpcperfstats --profile pg18-migrate exec -T db_pg18 sh -s < scripts/pg18_relational_copy.sh; echo "relational_exit=$?"
```

**Fail closed:** the output ends with `RELATIONAL_COPY_OK` and `relational_exit=0`. On `RELATIONAL_COPY_FAILED`, fix the cause and rerun D4 (it truncates and restores again).

---

## Phase E — cutover (site down until E1 completes)

`docker-compose.pg18-primary.yaml` (tracked) parks PG15 `db` under profile `pg15-retired`, starts `db_pg18` with the normal stack, gives it hostname `db`, and mounts the shared `/run/postgresql` socket that `web` and `pipeline` use. It is activated by adding it to the **site** `docker-compose.yaml` `include:` list. The site file is always loaded, so the cutover also holds for any `-f docker-compose.yaml` command. Do **not** use a `docker-compose.override.yaml` for this: `-f` skips it and would bring PG15 back as `db`.

### E1 — switch, start, verify (paste output)

```bash
podman-compose -p hpcperfstats stop db
grep -qx '  - docker-compose.pg18-primary.yaml' docker-compose.yaml || sed -i '/^  - docker-compose.defaults.yaml$/a\  - docker-compose.pg18-primary.yaml' docker-compose.yaml
grep -n -A3 '^include:' docker-compose.yaml; podman-compose -p hpcperfstats config --services | tr '\n' ' '; echo
podman-compose -p hpcperfstats up -d --force-recreate db_pg18
for i in $(seq 1 60); do podman-compose -p hpcperfstats exec -T db_pg18 pg_isready -h 127.0.0.1 -U hpcperfstats -d hpcperfstats -q && break; sleep 5; done
podman-compose -p hpcperfstats exec -T db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -X -c "SELECT add_compression_policy('host_data', compress_after => INTERVAL '8d', if_not_exists => true)"
podman-compose -p hpcperfstats up -d web pipeline proxy; podman-compose -p hpcperfstats ps
podman-compose -p hpcperfstats exec -T web /usr/local/bin/python3 -c '
from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
ensure_django()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT version(), inet_server_addr()")
    print("django_db", cur.fetchone())
    cur.execute("SHOW io_method")
    print("io_method", cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM job_data")
    print("job_data_n", cur.fetchone()[0])
'
```

**Fail closed:** the `include:` list shows `docker-compose.pg18-primary.yaml` right after `docker-compose.defaults.yaml`; `config --services` lists `db_pg18` and **not** `db`; `ps` shows `db_pg18`, `web`, `pipeline`, `proxy`, `valkey` and `rabbitmq` up. `django_db` shows PostgreSQL 18 and `None` (Unix socket), `io_method` is `io_uring`; `job_data_n` matches the D4 `pg18:` count. If `db_pg18` logs `lock file "/run/postgresql/.s.PGSQL.5432.lock" already exists`, PG15 did not shut down cleanly: run `podman-compose -p hpcperfstats up -d --force-recreate db_pg18 web pipeline`.

### E2 — rollback (only if E1 fails; PG18 writes after cutover are lost)

```bash
podman-compose -p hpcperfstats stop web pipeline proxy db_pg18
sed -i '/^  - docker-compose.pg18-primary.yaml$/d' docker-compose.yaml
podman-compose -p hpcperfstats up -d db web pipeline proxy; podman-compose -p hpcperfstats ps
```

Name services on every `up` during migration and cutover. After any compose-file change, a bare `podman-compose up -d` recreates **every** project container (stale config hash), including `db`, `db_pg18` and `rabbitmq` (30-minute stop grace each) and `valkey`; a service list limits that to the named services.

### After soak

Keep `/data/hpcperfstats_db/pg15` and the parked `db` container until the soak completes; deleting them is manual, never automatic. `./scripts/rebuild_full_site.sh` keeps working after cutover: it requires `db_pg18` instead of `db` once the overlay is included. The state file `/hpcperfstats/pg18_migrate/state.json` may be deleted after cutover.

---

## Image bake notes

| Item | Contract |
|------|----------|
| Base | `alpine:3.24.x` pinned in `services-conf/db.Dockerfile` (not `latest`) |
| Postgres / Timescale | PostgreSQL 18.x and TimescaleDB 2.31.x SHA-pinned (`ARG PG_VERSION`, `ARG TIMESCALEDB_VERSION`); not `APACHE_ONLY` |
| `/opt` | Source-built jemalloc, zlib-ng, ICU, liburing, lz4, zstd; rpath on `postgres` and the zstd CLI |
| Toolchain | Clang 22 ThinLTO, `-march=native` (`services-conf/bake_clang_flags.sh`); bake on the production CPU |
| Volume | `/var/lib/postgresql` (PG18 layout) |
| io_uring | `seccomp=unconfined` + `label=disable` + `cap_add: SYS_ADMIN` on `db_pg18`; host `kernel.io_uring_disabled=1` + `kernel.io_uring_group=70`; forbidden `disabled=2` |
| Runtime GUCs | `db_pg18` matches `db` (`shared_buffers=64GB`, WAL/bgwriter, committed `hba_file`) plus `io_method=io_uring` |

## Related

- `docker-compose.pg18-primary.yaml` (cutover overlay; include-listed per site)
- `scripts/pg18_host_data_chunk_copy.py`, `scripts/pg18_relational_copy.sh`
- `docs/upgrade.md` (dual-run note, PG18 bind and io_uring prerequisites)
- `docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md` (stay on 2.28.x while on PG15)
