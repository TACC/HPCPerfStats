# Migrate to PostgreSQL 18

This page moves a site from the Hub `timescale/timescaledb:2.28.3-pg15` server to the homemade `hpcperfstats-db:guard1` image (PostgreSQL 18 + TimescaleDB 2.32.1 on Alpine musl) by logical copy.
It is for site operators; use it while PG15 is still the live database.

Until cutover, the site `docker-compose.yaml` includes the tracked `docker-compose.pg15-primary.yaml`.
That include keeps PG15 as service `db` and adds the PG18 copy target `db_pg18` (profile `pg18-migrate`, alias `db18`).
Cutover deletes the include line, and `db` becomes PG18 under the same name, alias and socket.

> [!WARNING]
> Never use `pg_upgrade`, and never restore `_timescaledb_catalog` from PG15.
> Timescale 2.29 and later drop PG15, and musl and glibc collations differ, so PG18 gets a fresh Django-migrated schema and rows arrive by binary `COPY`.

## Prerequisites

- The image and service facts in [Compose services](../../reference/compose-services.md#db-image) and the contract rule `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`.
- A root shell in the checkout that holds `docker-compose.yaml`.
  Every block runs there, with `podman-compose -p hpcperfstats` and no `-f`.
- A `web` image built from code whose `0001_initial` and model declare `host_data.value`, `delta` and `arc` as `double precision`: Phase B migrates from that image.
- A clean checkout that fast-forwards (`git pull --ff-only`).

## How the guards keep each server on its own data

Each database server checks its data folder before it writes anything there:

- PG18 refuses a folder that holds a `PG_VERSION` other than 18 at the bind root, under `18/`, or at `data/`.
- PG18 as `db` (role `primary`) writes `hpcperfstats-primary` in the bind root before `initdb` on an empty bind and refuses an existing cluster without it.
  It also refuses an empty bind that carries `hpcperfstats-migrate-target`, so a reset copy target never becomes an empty live database.
- PG18 as `db_pg18` (role `migrate-target`) refuses a bind with `hpcperfstats-primary` and leaves `hpcperfstats-migrate-target` there.
- PG15 `db` (`services-conf/db-docker-pg15-guard.sh`) refuses anything but a PG15 cluster, any PG18 file, and `hpcperfstats-retired`.
- Both hold `flock` on `.hpc-server.lock` in their bind root for the life of the server, so a second server on the same folder (any project, any container) refuses to start.

Only `scripts/pg18_write_primary_marker.sh` writes the marker on a migrated site, and only after the freeze stamps prove a complete, verified copy of a read-only PG15.
The stamps are `pg15_readonly_ok` (D2), `verify_ok.json` (D3), `relational_copy_ok` (D4) and `pg15_writes_ok` (E1).

## Timeline and downtime

The phases run in order; only D2 to E stop anything.

| Phase                                       | Stack state                                   | Cost                                               |
| ------------------------------------------- | --------------------------------------------- | -------------------------------------------------- |
| Release step (once per PG15 site)           | Stack restarts once (rebuild)                 | Image bake + container restarts                    |
| 0 preflight, A build and start, B schema    | Live on PG15                                  | Minutes (A: image bake)                            |
| C bulk copy (`host_data`, PG18 indexes off) | Live on PG15                                  | Hours to days; rerun until clean                   |
| C2 restore PG18 indexes                     | Live on PG15                                  | Minutes to hours (recent uncompressed chunks only) |
| D1 catch-up pass                            | Live on PG15                                  | Minutes                                            |
| D2 and D3 pipeline stopped, PG15 read-only  | Ingest paused (RabbitMQ buffers monitor data) | Changed slices only                                |
| D4 web and proxy stopped, relational copy   | Site down                                     | Parallel dump and restore of the relational tables |
| E cutover                                   | Site down until E1 step 3 starts `web`        | Container restarts                                 |

## Release step: install the guards on a PG15 site

Run this once on every site that still serves PG15 before it pulls code where `db` is PG18.
The include line goes in before the pull, so no compose command can start PG18 as `db` on the copy target: until the pull lands, every compose command fails because the included file does not exist yet, and running containers keep running.
Never use a `docker-compose.override.yaml` for the include line or the cutover: a command that names `-f docker-compose.yaml` skips the override.

1. Stop the copy (if it runs) and `db_pg18`, then add the include line and look for a `db_pg18:` stanza in the site file:

   ```bash
   P="podman-compose -p hpcperfstats"
   F=docker-compose.yaml
   L='  - docker-compose.pg15-primary.yaml'
   C=hpcperfstats_pg18_copy
   if podman container exists $C; then
     podman stop -t 30 $C; fi
   $P --profile pg18-migrate stop db_pg18
   grep -qx "$L" $F \
     || sed -i "/^  - docker-compose.defaults.yaml\$/a\\$L" $F
   grep -n -A3 '^include:' $F
   grep -n -A3 '^  db_pg18:' $F
   ```

   Fail closed unless the `include:` list shows `docker-compose.pg15-primary.yaml` right after `docker-compose.defaults.yaml`.

2. If the last `grep` printed a `db_pg18:` stanza, delete that stanza from `docker-compose.yaml` in an editor.
   Leave any `db:` stanza (for example `ports:`) in place: list keys there apply to whichever server is `db`.

3. Pull and rebuild (the whole stack restarts once; PG15 stops with its 30-minute grace):

   ```bash
   git pull --ff-only && ./scripts/rebuild_full_site.sh
   ```

   `rebuild_full_site.sh` refuses before it builds or stops anything when the site file still names a `db_pg18:` service or the PG15 and PG18 binds do not match the include.
   After `down` it stops every container on the host that still mounts either bind and checks both locks are free before `up`.

4. Start `db_pg18` again and wait for it:

   ```bash
   P="podman-compose -p hpcperfstats --profile pg18-migrate"
   $P up -d --no-build db_pg18
   for i in $(seq 1 360); do
     $P exec -T db_pg18 pg_isready -h 127.0.0.1 -U hpcperfstats \
       -d postgres -q && break; sleep 5; done
   $P exec -T db_pg18 psql -h localhost -U hpcperfstats \
     -d hpcperfstats -XtAc 'SHOW io_method'
   ```

5. Restart the copy with the Phase C start block; it resumes from `state.json`.

Every database stop on this page goes through `podman-compose ... stop` or `podman stop -t 1800`: a plain `podman stop` or `podman rm -f` kills PostgreSQL after 10 seconds.

## How the copy keeps the window short

`scripts/pg18_host_data_chunk_copy.py` does the `host_data` copy:

- State file: Every verified chunk records `{rows, sig}`; `sig` is the chunk's PG15 `pg_stat` insert, update and delete counters.
  With `--changed-only`, a chunk whose signature has not moved is skipped without a query.
- Slice resync: A changed chunk that only gained rows since its verification is compared in 60-minute slices, and only mismatched slices are deleted and copied again.
  Any UPDATE or DELETE (operator repair tools) forces a whole-chunk copy.
- Compression off the copy workers: A finished chunk older than 8 days goes to a background compression pool (`--compress-workers`, default 2) while its worker moves on.
  PG18 never holds the full history uncompressed, and nothing is left to compress after cutover.
  Each run also compresses PG18 chunks that have aged past 8 days; the freeze pass (`--verify`) never compresses.
- Chunks created up front and outside the COPY: TimescaleDB holds `ShareUpdateExclusiveLock` on `host_data` from chunk creation until the creating transaction ends, so a chunk created inside its COPY queues every other worker behind it.
  `compress_chunk` also blocks chunk creation while it runs (a new-chunk insert waited out a 29 s compress).
  Each run therefore creates every missing PG18 chunk before copying, through a committed placeholder insert and delete.
- Binary COPY: An explicit, type-checked column list fails closed if the PG15 and PG18 `host_data` layouts differ.
  The one allowed difference: `value`, `delta` and `arc` are `real` on PG15 and `double precision` on PG18; the source `SELECT` casts exactly those three, and the run logs `widening real -> double precision: arc,delta,value`.
- No index maintenance during the bulk copy: Phase B saves and drops the six `host_data` indexes (`--drop-target-indexes`); maintaining them on 400-700M-row days meant random index reads at about 1,600 rows/s.
  C2 rebuilds them before the freeze; on compressed chunks the heap is empty, so only the recent uncompressed chunks cost build time.
- `--verify` (freeze only): Recounts each copied chunk on both sides and exits non-zero on a mismatch; its `verified_rows=` total replaces a full `count(*)` of `host_data`.
- Per-chunk failures: A failed chunk is logged and dropped from the state file; the run continues and exits 1, and the same command retries it.
- Stamps: every run that writes PG18 first deletes `/hpcperfstats/pg18_migrate/verify_ok.json`; only a `--verify` run that exits 0 writes it again.

Between D1 and D3, run no `host_data` UPDATE or DELETE tool on PG15 (`repair_jstamp_deltas`, `scripts/retired/backfill_host_data_null_dev.sh`, `scripts/retired/collapse_percore_parallel.sh`).
The chunks they touch fall back to whole-chunk copies: correct, but slower.

## Phase 0: check the host (read-only; paste the output)

PG18 compresses as it copies, so its steady-state size is about the PG15 `db_size`, plus in-flight uncompressed chunks and up to `max_wal_size=64GB` of WAL.
Both servers set `shared_buffers=64GB`; bulk `COPY`, counts and compression use PostgreSQL's small ring buffers, so PG18 does not fill its 64 GB during dual-run.

```bash
hostname; git log -1 --oneline; free -g | head -2
grep -n -A5 -E '^  postgres_data(_pg18)?:' docker-compose.yaml \
  | grep -E 'postgres_data|device'
grep -n -A3 '^include:' docker-compose.yaml
df -h /data/hpcperfstats_db 2>&1 | tail -1
S1='SELECT pg_size_pretty(pg_database_size(current_database()))
  AS pg15_db_size'
S2="SELECT pg_size_pretty(sum(before_compression_total_bytes))
  AS before, pg_size_pretty(sum(after_compression_total_bytes))
  AS after FROM hypertable_compression_stats('host_data')"
S3="SELECT count(*) AS chunks,
  count(*) FILTER (WHERE is_compressed) AS compressed,
  min(range_start)::date AS oldest, max(range_end)::date AS newest
  FROM timescaledb_information.chunks
  WHERE hypertable_name = 'host_data'"
S4="SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid))
  AS size FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND c.relname <> 'host_data'
  ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 8"
podman-compose -p hpcperfstats exec -T db psql -h localhost \
  -U hpcperfstats -d hpcperfstats -X \
  -c 'SET statement_timeout = 0' -c "$S1" -c "$S2" -c "$S3" -c "$S4"
```

Fail closed unless free space on the `pg18` filesystem is at least 1.2 x the PG15 `db_size` + 80 GB, `docker-compose.yaml` has a device for `postgres_data_pg18`, and the `include:` list shows `docker-compose.pg15-primary.yaml` (Release step).

## Phase A: build and start PG18 beside PG15

`db` stays the live writer.
PG18 runs as `db_pg18` from the legacy include: profile `pg18-migrate`, alias `db18`, on the bind `postgres_data_pg18`.
The bind replaces the image ownership, so it must belong to the Alpine postgres uid and gid 70; `root:root` fails with `mkdir ... /18/: Permission denied`.
Bake on the production CPU (`-march=native`).

```bash
D=/data/hpcperfstats_db/pg18
mkdir -p $D && chown -R 70:70 $D && ls -ld $D
podman build -f services-conf/db.Dockerfile \
  -t hpcperfstats-db:guard1 services-conf
podman-compose -p hpcperfstats --profile pg18-migrate \
  up -d --no-build db_pg18
X="podman-compose -p hpcperfstats --profile pg18-migrate exec -T"
for i in $(seq 1 60); do
  $X db_pg18 pg_isready -h 127.0.0.1 -U hpcperfstats -d postgres \
    -q && break; sleep 5; done
Q="psql -h localhost -U hpcperfstats -X"
E="SELECT 1 FROM pg_database WHERE datname = 'hpcperfstats'"
$X db_pg18 $Q -d postgres -tAc "$E" | grep -q 1 \
  || $X db_pg18 $Q -d postgres -v ON_ERROR_STOP=1 \
    -c 'CREATE DATABASE hpcperfstats OWNER hpcperfstats'
V="SELECT default_version FROM pg_available_extensions
  WHERE name = 'timescaledb'"
$X db_pg18 $Q -d hpcperfstats -c 'SELECT version()' \
  -c 'SHOW io_method' -c 'SHOW shared_buffers' -c 'SHOW hba_file' \
  -c "$V"
```

Fail closed unless `ls -ld` shows owner `70 70`, `version()` is PostgreSQL 18, `io_method` is `worker`, and `hba_file` is `/etc/postgresql/pg_hba.conf`.
A different `io_method` means the container runs an older compose file: pull, then recreate it with `podman-compose -p hpcperfstats --profile pg18-migrate up -d --force-recreate --no-build db_pg18`.
The database create step is idempotent, so a rerun after an early crash loop is safe.

## Phase B: create the empty schema on PG18

The deployed `web` image must carry the current migrations: `machine.0001_initial` creates the timescaledb extension before `create_hypertable`.
The long-running `web` keeps using PG15.
This one-shot points Django at `db18` and migrates; the checks then confirm an empty `host_data` hypertable and remove the compression policy, so the copy script owns compression until cutover.

```bash
podman-compose -p hpcperfstats --profile pg18-migrate run --rm \
  --no-deps --entrypoint /usr/local/bin/python3 web -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE",
    "hpcperfstats.site.hpcperfstats_site.settings")
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
A="SELECT 'host_data_n=' || count(*) FROM host_data"
B="SELECT 'compression_enabled=' || compression_enabled
  FROM timescaledb_information.hypertables
  WHERE hypertable_name = 'host_data'"
C="SELECT remove_compression_policy('host_data', if_exists => true)"
J="SELECT 'compression_jobs=' || count(*)
  FROM timescaledb_information.jobs
  WHERE hypertable_name = 'host_data'
  AND proc_name LIKE '%compression%'"
T="SELECT 'value_types=' || string_agg(
  column_name || ':' || data_type, ',' ORDER BY column_name)
  FROM information_schema.columns WHERE table_name = 'host_data'
  AND column_name IN ('arc', 'delta', 'value')"
podman-compose -p hpcperfstats --profile pg18-migrate \
  exec -T db_pg18 psql -h localhost -U hpcperfstats \
  -d hpcperfstats -XtA -v ON_ERROR_STOP=1 \
  -c "$A" -c "$B" -c "$C" -c "$J" -c "$T"
podman-compose -p hpcperfstats --profile pg18-migrate \
  run --rm --no-deps -T -e PGPASSWORD=hpcperfstats \
  -v "$PWD/scripts:/migrate:ro" \
  --entrypoint /usr/local/bin/python3 web \
  -u /migrate/pg18_host_data_chunk_copy.py --target-host db18 \
  --state-file /hpcperfstats/pg18_migrate/state.json \
  --drop-target-indexes 2>&1 | grep -E 'dropped|ERROR|Error'
```

The last command saves the PG18 `host_data` index definitions to `/hpcperfstats/pg18_migrate/state.indexes.json` before it drops them; C2 rebuilds them from that file, and a rerun never overwrites it.
The checks run in `psql`, because the `web` cursor rejects parameterized `cursor.execute(sql, params)` (`the query has 1 placeholders but 2 parameters were passed`).
Rerunning the block is safe: `migrate` is idempotent and the policy removal uses `if_exists`.

Fail closed unless the output shows `server` PostgreSQL 18, `migrate_ok`, `host_data_n=0`, `compression_enabled=true`, `compression_jobs=0`, `value_types=arc:double precision,delta:double precision,value:double precision` and `target indexes dropped=6`.
`create_hypertable ... does not exist` means the `web` image predates the `0001` extension fix: redeploy it, then rerun.

## Phase C: copy `host_data` in bulk (live, rerunnable)

The copy runs detached in a one-shot `web` container with the checkout's script.
State persists in `/hpcperfstats/pg18_migrate/state.json` on the `hpcperfstatsdata` bind, so reruns resume.
`--watermark-days -7` selects every chunk including today's, and slice resync keeps repeat passes over hot chunks cheap.
Start with `--workers 4` and lower it if PG15 ingest latency suffers.

### Start the copy (web; paste the output)

```bash
podman rm -f hpcperfstats_pg18_copy 2>/dev/null
podman-compose -p hpcperfstats --profile pg18-migrate \
  run -d --no-deps --name hpcperfstats_pg18_copy \
  -e PGPASSWORD=hpcperfstats -v "$PWD/scripts:/migrate:ro" \
  --entrypoint /usr/local/bin/python3 web \
  -u /migrate/pg18_host_data_chunk_copy.py \
  --source-host db --target-host db18 --watermark-days -7 \
  --changed-only \
  --state-file /hpcperfstats/pg18_migrate/state.json \
  --workers 4 && podman update --restart=no hpcperfstats_pg18_copy
```

One-shot `run -d` containers inherit `restart: always` from `web`.
Without `podman update --restart=no`, a finished copy (even `exit 0`) starts another full pass, and a failing C2 retries in a loop.
Every detached start on this page turns it off; remove the copy container (`podman rm -f hpcperfstats_pg18_copy`) before C2.

### Watch progress (host; Ctrl-C stops watching, not the copy)

```bash
podman logs -f hpcperfstats_pg18_copy 2>&1 \
  | grep --line-buffered -E ' rows=|ERROR|INFO done'
```

The script prints a progress line every 60 s (`--progress-seconds`): `HH:MM:SS rows=X/T (P%) copies_in_flight=N rows_per_s=R eta=Eh`.
`rows` adds the rows already verified in the state file (so it survives restarts), the rows of chunks finished this run, and the rows its PG15 `COPY TO` sessions are streaming.
Those sessions carry `application_name=pg18_chunk_copy`, so the `update_metrics` `COPY TO` is never counted.
`T` is the PG15 `approximate_row_count('host_data')`; rate and ETA count from the run start.
Drop the `grep` to also see each chunk start (`INFO copy chunk`).

The copy is done when the container shows `status=exited exit=0` and the `INFO done` line shows `failed=0`.
On `exit=1`, read the `ERROR chunk failed` lines and rerun the start block: failed chunks are retried and verified chunks are skipped.
Each rerun touches only chunks written since the last one.

To sync newer days later, rerun the start block at any time, even weeks after the bulk copy.
New PG15 day chunks are not in the state file, so they are copied; older days with late rows are resynced by slice, and days past 8 days are compressed.
If C2 already restored the indexes and many large days have piled up, drop the indexes again first (`--drop-target-indexes` keeps the saved definitions), rerun, then repeat C2.
The freeze pass refuses to start until the indexes are back.

### C2: restore the PG18 indexes (live; before D1)

Run C2 after the bulk copy exits cleanly and before D1; the copy must not be running, because the rebuild locks `host_data`.
The five non-unique indexes build one chunk per transaction; TimescaleDB refuses that for UNIQUE, so the unique index builds in one transaction.
`--maintenance-work-mem 2GB` bounds memory per build (16 GB x 8 workers OOM-killed Postgres).

```bash
podman rm -f hpcperfstats_pg18_indexes 2>/dev/null
podman-compose -p hpcperfstats --profile pg18-migrate \
  run -d --no-deps --name hpcperfstats_pg18_indexes \
  -e PGPASSWORD=hpcperfstats -v "$PWD/scripts:/migrate:ro" \
  --entrypoint /usr/local/bin/python3 web \
  -u /migrate/pg18_host_data_chunk_copy.py --target-host db18 \
  --state-file /hpcperfstats/pg18_migrate/state.json \
  --restore-target-indexes --maintenance-work-mem 2GB \
  && podman update --restart=no hpcperfstats_pg18_indexes \
  && podman logs -f hpcperfstats_pg18_indexes
```

Fail closed unless the log ends with `target indexes created=...` and `podman inspect hpcperfstats_pg18_indexes --format '{{.State.ExitCode}}'` prints `0`.
A rerun creates only the indexes still missing.
`RuntimeError: invalid host_data index after restore` means a build was interrupted: rerun this block.

## Phase D: freeze (downtime) and copy the relational tables

Schedule D1 through E back to back.
D2 makes PG15 read-only, so `web` keeps serving reads but every write (logins included) fails until cutover or E2.
After D4, any start of `web`, `proxy` or `pipeline` (including `scripts/rebuild_pipeline.sh`) means running D4 again; any PG15 restart after D2 means running D2 to D4 again.
The marker script in E1 checks both.

### D1: catch up while live (paste the output)

```bash
podman rm -f hpcperfstats_pg18_copy 2>/dev/null
podman-compose -p hpcperfstats --profile pg18-migrate \
  run --rm --no-deps -e PGPASSWORD=hpcperfstats \
  -v "$PWD/scripts:/migrate:ro" \
  --entrypoint /usr/local/bin/python3 web \
  -u /migrate/pg18_host_data_chunk_copy.py \
  --source-host db --target-host db18 --watermark-days -7 \
  --changed-only \
  --state-file /hpcperfstats/pg18_migrate/state.json \
  --workers 4 2>&1 | grep -E 'ERROR|INFO done'
```

Fail closed unless the catch-up shows `failed=0`.

### D2: stop the pipeline and make PG15 read-only (paste the output)

`ALTER SYSTEM` persists `default_transaction_read_only = on` in PG15's `postgresql.auto.conf`, so it also holds after a PG15 restart.
The `sleep 15` lets PG15 flush the last per-backend statistics first.
`--stamp-readonly` checks the setting, records the PG15 write counters (`tup_inserted + tup_updated + tup_deleted` over `pg_stat_database`) and both servers' system identifiers, and writes the `pg15_readonly_ok` stamp into the PG18 bind.

```bash
P="podman-compose -p hpcperfstats"
$P stop pipeline
$P ps pipeline
R="ALTER SYSTEM SET default_transaction_read_only = on"
$P exec -T db psql -h localhost -U hpcperfstats -d hpcperfstats \
  -X -v ON_ERROR_STOP=1 -c "$R" -c 'SELECT pg_reload_conf()' \
  -c 'SHOW default_transaction_read_only'
Q="SELECT pid, application_name, client_addr, state,
  left(query, 80) AS query FROM pg_stat_activity
  WHERE datname = current_database() AND pid <> pg_backend_pid()
  AND (query ILIKE '%host_data%' OR state <> 'idle') ORDER BY pid"
$P exec -T db psql -h localhost \
  -U hpcperfstats -d hpcperfstats -X -c "$Q"
sleep 15
./scripts/pg18_write_primary_marker.sh --stamp-readonly
echo "stamp_exit=$?"
```

Fail closed unless `pipeline` is stopped, `default_transaction_read_only` shows `on`, no `INSERT` or `COPY` into `host_data` remains in `pg_stat_activity` (web read queries are fine), and `stamp_exit=0`.

### D3: run the freeze pass with verification (paste the output)

```bash
podman-compose -p hpcperfstats --profile pg18-migrate \
  run --rm --no-deps -e PGPASSWORD=hpcperfstats \
  -v "$PWD/scripts:/migrate:ro" \
  --entrypoint /usr/local/bin/python3 web \
  -u /migrate/pg18_host_data_chunk_copy.py \
  --source-host db --target-host db18 --watermark-days -7 \
  --changed-only --verify \
  --state-file /hpcperfstats/pg18_migrate/state.json \
  --workers 4 --progress-seconds 0 2>&1 \
  | grep -E 'slice resync|refusing|ERROR|INFO done'
echo "freeze_exit=${PIPESTATUS[0]}"
```

`--verify` refuses to start (exit 2, `refusing --verify: ...`) unless PG15 shows `default_transaction_read_only` on, every index saved in Phase B is back and valid, and `host_data` has a valid UNIQUE index.
Ingest writes `ON CONFLICT (time, host, type, event, dev) DO NOTHING`, which fails without that index.
On an index refusal, run C2, then D3 again; on a read-only refusal, run D2 again.

Fail closed unless the output shows `freeze_exit=0` and `failed=0`.
The run writes `/hpcperfstats/pg18_migrate/verify_ok.json` only on exit 0.
Record `verified_rows=` for the change log.
On a failure, rerun D3; it examines only the failed chunks.

### D4: stop web and proxy, copy the relational tables (paste the output)

`scripts/pg18_relational_copy.sh` runs inside `db_pg18` and fails closed.
It refuses unless PG15 is read-only, checks that both sides have the same table set, then:

1. Runs a parallel `pg_dump -Fd` from `db`.
2. Runs `TRUNCATE ... RESTART IDENTITY CASCADE` on PG18.
3. Runs a parallel `pg_restore --data-only --disable-triggers`.
4. Compares exact per-table row counts, which must match, then runs `ANALYZE` and writes the `relational_copy_ok` stamp.

It excludes `host_data` and `django_migrations`.

```bash
podman-compose -p hpcperfstats stop web proxy
podman-compose -p hpcperfstats ps web proxy
podman-compose -p hpcperfstats --profile pg18-migrate \
  exec -T db_pg18 sh -s < scripts/pg18_relational_copy.sh
echo "relational_exit=$?"
```

Fail closed unless the output ends with `RELATIONAL_COPY_OK` and `relational_exit=0`.
On `RELATIONAL_COPY_FAILED`, fix the cause and rerun D4; it truncates and restores again.

## Phase E: cut over (site down until E1 step 3)

Cutover deletes the `docker-compose.pg15-primary.yaml` line from the `include:` list of the site `docker-compose.yaml`.
`db` then resolves to the PG18 service of `docker-compose.defaults.yaml` (role `primary`), which starts on the PG18 bind only after the marker script wrote `hpcperfstats-primary`.
The site file always loads, so the cutover also holds for any command that names `-f docker-compose.yaml`.
E1 checks PG18 before any ingest: until step 4 starts `pipeline`, E2 can still return to PG15 with nothing to reconcile.

### E1: switch, start and verify (paste the output of each step)

1. Stop the writers, confirm PG15 took no writes since D2, stop both servers, and write the marker:

   ```bash
   P="podman-compose -p hpcperfstats"
   M=./scripts/pg18_write_primary_marker.sh
   $P stop web proxy pipeline \
     && $M --check-writes \
     && $P --profile pg18-migrate stop db_pg18 \
     && $P stop db \
     && $M --dry-run && $M
   echo "marker_exit=$?"
   ```

   `--check-writes` compares PG15's write counters with the D2 stamp and writes `pg15_writes_ok` only when they did not move (a client that used `BEGIN READ WRITE` moves them).
   It first removes any earlier `pg15_writes_ok`, so a refusal leaves none behind.
   The marker script then refuses unless `web`, `proxy` and `pipeline` stopped after D4, PG15 last started before D2, both servers are stopped and both locks are free, and all four stamps carry the current PG15 and PG18 system identifiers in order.
   It writes `hpcperfstats-primary` in the PG18 bind and `hpcperfstats-retired` in the PG15 bind, and removes `hpcperfstats-migrate-target`.
   Fail closed unless `marker_exit=0`; on a refusal, nothing changed: fix the cause it names (often D4 or D2 again).

2. Delete the include line, remove the old `db_pg18` container and start PG18 as `db`:

   ```bash
   F=docker-compose.yaml
   sed -i '/^  - docker-compose.pg15-primary.yaml$/d' $F
   grep -n -A3 '^include:' $F
   podman rm hpcperfstats_db_pg18_1
   P="podman-compose -p hpcperfstats"
   $P up -d --no-build db
   for i in $(seq 1 360); do
     $P exec -T db pg_isready -h 127.0.0.1 -U hpcperfstats \
       -d hpcperfstats -q && break; sleep 5; done
   $P ps db
   ```

   Fail closed unless the `include:` list shows only `docker-compose.defaults.yaml` and `db` is up.

3. Start `web` only and run the checks:

   ```bash
   P="podman-compose -p hpcperfstats"
   $P up -d --no-build web
   X="$P exec -T db psql -h localhost -U hpcperfstats"
   X="$X -d hpcperfstats -XtAc"
   W="FROM pg_index WHERE indrelid = 'public.host_data'::regclass"
   uniq=$($X "SELECT count(*) $W AND indisunique AND indisvalid")
   bad=$($X "SELECT count(*) $W AND NOT indisvalid")
   echo "pg18_host_data_unique_indexes=$uniq invalid_indexes=$bad"
   $P exec -T web /usr/local/bin/python3 -c '
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

   Fail closed unless `django_db` shows PostgreSQL 18 and `None` (Unix socket), `io_method` is `worker`, `job_data_n` matches the D4 `pg18:` count, `pg18_host_data_unique_indexes` is at least 1 and `invalid_indexes=0`.
   On any failure, run E2: no ingest has reached PG18 yet.

4. Only after step 3 passed, add the compression policy and start ingest:

   ```bash
   P="podman-compose -p hpcperfstats"
   X="$P exec -T db psql -h localhost -U hpcperfstats"
   X="$X -d hpcperfstats -XtAc"
   W="FROM pg_index WHERE indrelid = 'public.host_data'::regclass"
   uniq=$($X "SELECT count(*) $W AND indisunique AND indisvalid")
   bad=$($X "SELECT count(*) $W AND NOT indisvalid")
   if [ "${uniq:-0}" -ge 1 ] && [ "$bad" = "0" ]; then
   R="SELECT add_compression_policy('host_data',
     compress_after => INTERVAL '8d', if_not_exists => true)"
   $X "$R"
   $P up -d --no-build pipeline proxy
   $P ps
   else echo "CUTOVER_BLOCKED: PG18 host_data has no valid UNIQUE"
   echo "index or has invalid indexes; run E2."; fi
   ```

   Ingest's `ON CONFLICT` needs the UNIQUE index, so the block refuses to start `pipeline` without it.
   Fail closed unless `ps` shows `db`, `web`, `pipeline`, `proxy`, `valkey` and `rabbitmq` up.

If `db` logs `lock file "/run/postgresql/.s.PGSQL.5432.lock" already exists`, PG15 did not shut down cleanly: run `podman-compose -p hpcperfstats up -d --force-recreate --no-build db`.

### E2: roll back (only before E1 step 4 starts the pipeline)

`--rollback` refuses once `pipeline` has started on PG18; from then on, fix forward on PG18.
It removes the marker, the retired file and the stamps, and restores `hpcperfstats-migrate-target`.

```bash
P="podman-compose -p hpcperfstats"
F=docker-compose.yaml
L='  - docker-compose.pg15-primary.yaml'
$P stop web db \
  && ./scripts/pg18_write_primary_marker.sh --rollback \
  && { grep -qx "$L" $F \
    || sed -i "/^  - docker-compose.defaults.yaml\$/a\\$L" $F; } \
  && grep -n -A3 '^include:' $F \
  && $P up -d --no-build db
for i in $(seq 1 360); do
  $P exec -T db pg_isready -h 127.0.0.1 -U hpcperfstats \
    -d hpcperfstats -q && break; sleep 5; done
U="ALTER SYSTEM RESET default_transaction_read_only"
$P exec -T db psql -h localhost -U hpcperfstats -d hpcperfstats \
  -X -v ON_ERROR_STOP=1 -c "$U" -c 'SELECT pg_reload_conf()' \
  -c 'SHOW default_transaction_read_only' -c 'SELECT version()'
$P up -d --no-build web pipeline proxy
$P ps
```

Fail closed unless `version()` shows PostgreSQL 15, `default_transaction_read_only` shows `off`, and `ps` shows the stack up.
To try again, start the copy target (`podman-compose -p hpcperfstats --profile pg18-migrate up -d --no-build db_pg18`; E1 step 2 removed its container), then run D1 to E1 again.

Name the services on every `up` during migration and cutover.
After any compose-file change, a bare `podman-compose up -d` recreates every project container (stale config hash), including `db`, `db_pg18`, `rabbitmq` (30-minute stop grace each) and `valkey`.

### After the soak

Keep `/data/hpcperfstats_db/pg15` and the Hub `timescale/timescaledb:2.28.3-pg15` image until the soak completes; deleting them is manual, never automatic.
No container serves the PG15 bind after E1, and its `hpcperfstats-retired` file makes PG15 refuse it.
`./scripts/rebuild_full_site.sh` keeps working after cutover: without the include it builds and starts PG18 `db`.
You can delete the state file `/hpcperfstats/pg18_migrate/state.json` after cutover.

## Troubleshooting

- `copies_in_flight` stays at `1` while `--workers` is higher: check PG18 for `Lock:relation` waits on `host_data`.
  That is the chunk-creation lock the script avoids, so an older script without chunk pre-creation is running.

- `hpcperfstats db guard: refusing to start` or `hpcperfstats pg15 guard: refusing to start` in a database log: the server found the wrong data, a missing or present marker, or a held lock, and changed nothing.
  The message names the path and the reason; do not delete marker, sentinel or lock files by hand to get past it.

- Phase B prints `value_types=` with `real` columns: that PG18 database predates the `double precision` schema and must be rebuilt from an empty database.
  This works only while PG15 is authoritative (before cutover).
  No migration retypes the columns: PG15 applied `0001` long ago and keeps `real`, and redeploying `web` does not change its schema.
  Stop the copy and any index restore, drop and recreate the PG18 database, delete the freeze stamps, and move the old state files aside; PG15, `web`, `pipeline` and the other containers stay untouched:

  ```bash
  K="hpcperfstats_pg18_copy hpcperfstats_pg18_indexes"
  podman stop -t 30 $K 2>/dev/null
  podman rm -f $K 2>/dev/null
  P="podman-compose -p hpcperfstats --profile pg18-migrate"
  D="DROP DATABASE IF EXISTS hpcperfstats WITH (FORCE)"
  C="CREATE DATABASE hpcperfstats OWNER hpcperfstats"
  $P exec -T db_pg18 psql -h localhost -U hpcperfstats -d postgres \
    -XtA -v ON_ERROR_STOP=1 -c "$D" -c "$C"
  S=/var/lib/postgresql
  $P exec -T db_pg18 rm -f $S/pg15_readonly_ok \
    $S/relational_copy_ok $S/pg15_writes_ok
  M='cd /hpcperfstats/pg18_migrate && rm -f verify_ok.json
    for f in state*.json; do
    [ -e "$f" ] && mv "$f" "$f.pre-double"; done; ls'
  $P run --rm --no-deps -T --entrypoint sh web -c "$M"
  ```

  Then run Phase B, Phase C and the rest of this page as for a first migration.
  The `*.pre-double` files are only a record; nothing reads them.

## Related

- [Compose services: db image](../../reference/compose-services.md#db-image)
- [Upgrade the stack](upgrade-the-stack.md#upgrade-the-timescale-catalog-on-postgresql-15)
- `docker-compose.pg15-primary.yaml`, `scripts/pg18_host_data_chunk_copy.py`, `scripts/pg18_relational_copy.sh`, `scripts/pg18_write_primary_marker.sh`, `scripts/rebuild_full_site.sh`
- `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`
