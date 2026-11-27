# Migrate to PostgreSQL 18

This page moves a site from the Hub `timescale/timescaledb:2.28.3-pg15` service `db` to the homemade `hpcperfstats-db` image (PostgreSQL 18 + TimescaleDB 2.32.1 on Alpine musl, service `db_pg18`) by logical copy.
It is for site operators; use it while PG15 is still the live database.

> [!WARNING]
> Never use `pg_upgrade`, and never restore `_timescaledb_catalog` from PG15.
> Timescale 2.29 and later drop PG15, and musl and glibc collations differ, so PG18 gets a fresh Django-migrated schema and rows arrive by binary `COPY`.

## Prerequisites

- The image and service facts in [Compose services](../../reference/compose-services.md#db-image) and the contract rule `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`.
- A root shell in the checkout that holds `docker-compose.yaml`.
  Every block runs there, with `podman-compose -p hpcperfstats` and no `-f`.
- A `web` image built from code whose `0001_initial` and model declare `host_data.value`, `delta` and `arc` as `double precision`: Phase B migrates from that image.

## Timeline and downtime

The phases run in order; only D2 to E stop anything.

| Phase                                       | Stack state                                   | Cost                                               |
| ------------------------------------------- | --------------------------------------------- | -------------------------------------------------- |
| 0 preflight, A build and start, B schema    | Live on PG15                                  | Minutes (A: image bake)                            |
| C bulk copy (`host_data`, PG18 indexes off) | Live on PG15                                  | Hours to days; rerun until clean                   |
| C2 restore PG18 indexes                     | Live on PG15                                  | Minutes to hours (recent uncompressed chunks only) |
| D1 catch-up pass                            | Live on PG15                                  | Minutes                                            |
| D2 and D3 pipeline stopped, freeze pass     | Ingest paused (RabbitMQ buffers monitor data) | Changed slices only                                |
| D4 web and proxy stopped, relational copy   | Site down                                     | Parallel dump and restore of the relational tables |
| E cutover                                   | Site down until E1 completes                  | Container restarts                                 |

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

Between D1 and D3, run no `host_data` UPDATE or DELETE tool on PG15 (`repair_jstamp_deltas`, `scripts/retired/backfill_host_data_null_dev.sh`, `scripts/retired/collapse_percore_parallel.sh`).
The chunks they touch fall back to whole-chunk copies: correct, but slower.

## Phase 0: check the host (read-only; paste the output)

PG18 compresses as it copies, so its steady-state size is about the PG15 `db_size`, plus in-flight uncompressed chunks and up to `max_wal_size=64GB` of WAL.
Both servers set `shared_buffers=64GB`; bulk `COPY`, counts and compression use PostgreSQL's small ring buffers, so PG18 does not fill its 64 GB during dual-run.

```bash
hostname; git log -1 --oneline; free -g | head -2
grep -n -A5 -E '^  postgres_data(_pg18)?:' docker-compose.yaml \
  | grep -E 'postgres_data|device'
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

Fail closed unless free space on the `pg18` filesystem is at least 1.2 x the PG15 `db_size` + 80 GB and `docker-compose.yaml` has a device for `postgres_data_pg18`.

## Phase A: build and start PG18 beside PG15

`db` stays the live writer.
PG18 runs as profile `pg18-migrate`, alias `db18`, on the bind `postgres_data_pg18`.
The bind replaces the image ownership, so it must belong to the Alpine postgres uid and gid 70; `root:root` fails with `mkdir ... /18/: Permission denied`.
Bake on the production CPU (`-march=native`).

```bash
D=/data/hpcperfstats_db/pg18
mkdir -p $D && chown -R 70:70 $D && ls -ld $D
podman build -f services-conf/db.Dockerfile -t hpcperfstats-db \
  services-conf
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

### D1 and D2: catch up while live, then stop the pipeline (paste the output)

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
podman-compose -p hpcperfstats stop pipeline
podman-compose -p hpcperfstats ps pipeline
Q="SELECT pid, application_name, client_addr, state,
  left(query, 80) AS query FROM pg_stat_activity
  WHERE datname = current_database() AND pid <> pg_backend_pid()
  AND (query ILIKE '%host_data%' OR state <> 'idle') ORDER BY pid"
podman-compose -p hpcperfstats exec -T db psql -h localhost \
  -U hpcperfstats -d hpcperfstats -X -c "$Q"
```

Fail closed unless the catch-up shows `failed=0`, `pipeline` is stopped, and no `INSERT` or `COPY` into `host_data` remains in `pg_stat_activity` (web read queries are fine).

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

`--verify` refuses to start (exit 2, `refusing --verify: target host_data indexes not restored`) unless every index saved in Phase B is back and valid and `host_data` has a valid UNIQUE index.
Ingest writes `ON CONFLICT (time, host, type, event, dev) DO NOTHING`, which fails without that index.
On a refusal, run C2, then D3 again.

Fail closed unless the output shows `freeze_exit=0` and `failed=0`.
Record `verified_rows=` for the change log.
On a failure, rerun D3; it examines only the failed chunks.

### D4: stop web and proxy, copy the relational tables (paste the output)

`scripts/pg18_relational_copy.sh` runs inside `db_pg18` and fails closed.
It checks that both sides have the same table set, then:

1. Runs a parallel `pg_dump -Fd` from `db`.
2. Runs `TRUNCATE ... RESTART IDENTITY CASCADE` on PG18.
3. Runs a parallel `pg_restore --data-only --disable-triggers`.
4. Compares exact per-table row counts, which must match, then runs `ANALYZE`.

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

## Phase E: cut over (site down until E1 completes)

The tracked `docker-compose.pg18-primary.yaml` parks PG15 `db` under profile `pg15-retired`, starts `db_pg18` with the normal stack under hostname `db`, and mounts the shared `/run/postgresql` socket that `web` and `pipeline` use.
Adding it to the `include:` list of the site `docker-compose.yaml` activates it.
The site file always loads, so the cutover also holds for any command that names `-f docker-compose.yaml`.
Never use a `docker-compose.override.yaml` for the cutover: `-f` skips it and brings PG15 back as `db`.

### E1: switch, start and verify (paste the output)

```bash
P="podman-compose -p hpcperfstats"
X="$P --profile pg18-migrate exec -T db_pg18 psql -h localhost"
X="$X -U hpcperfstats -d hpcperfstats -XtAc"
W="FROM pg_index WHERE indrelid = 'public.host_data'::regclass"
uniq=$($X "SELECT count(*) $W AND indisunique AND indisvalid")
bad=$($X "SELECT count(*) $W AND NOT indisvalid")
echo "pg18_host_data_unique_indexes=$uniq invalid_indexes=$bad"
if [ "${uniq:-0}" -ge 1 ] && [ "$bad" = "0" ]; then
podman-compose -p hpcperfstats stop db
F=docker-compose.yaml
L='  - docker-compose.pg18-primary.yaml'
grep -qx "$L" $F \
  || sed -i "/^  - docker-compose.defaults.yaml\$/a\\$L" $F
grep -n -A3 '^include:' $F
$P config --services | tr '\n' ' '; echo
podman-compose -p hpcperfstats up -d --force-recreate db_pg18
for i in $(seq 1 60); do
  $P exec -T db_pg18 pg_isready -h 127.0.0.1 -U hpcperfstats \
    -d hpcperfstats -q && break; sleep 5; done
R="SELECT add_compression_policy('host_data',
  compress_after => INTERVAL '8d', if_not_exists => true)"
$P exec -T db_pg18 psql -h localhost -U hpcperfstats \
  -d hpcperfstats -X -c "$R"
podman-compose -p hpcperfstats up -d web pipeline proxy
$P ps
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
else echo "CUTOVER_BLOCKED: PG18 host_data has no valid UNIQUE"
echo "index or has invalid indexes; run C2. PG15 is untouched."; fi
```

The gate comes first: nothing stops or switches unless `pg18_host_data_unique_indexes` is at least 1 and `invalid_indexes=0`, because ingest's `ON CONFLICT` needs the UNIQUE index.

Fail closed unless:

- The `include:` list shows `docker-compose.pg18-primary.yaml` right after `docker-compose.defaults.yaml`.
- `config --services` lists `db_pg18` and not `db`.
- `ps` shows `db_pg18`, `web`, `pipeline`, `proxy`, `valkey` and `rabbitmq` up.
- `django_db` shows PostgreSQL 18 and `None` (Unix socket), `io_method` is `worker`, and `job_data_n` matches the D4 `pg18:` count.

If `db_pg18` logs `lock file "/run/postgresql/.s.PGSQL.5432.lock" already exists`, PG15 did not shut down cleanly: run `podman-compose -p hpcperfstats up -d --force-recreate db_pg18 web pipeline`.

### E2: roll back (only if E1 fails; PG18 writes after cutover are lost)

```bash
podman-compose -p hpcperfstats stop web pipeline proxy db_pg18
sed -i '/^  - docker-compose.pg18-primary.yaml$/d' \
  docker-compose.yaml
podman-compose -p hpcperfstats up -d db web pipeline proxy
podman-compose -p hpcperfstats ps
```

Name the services on every `up` during migration and cutover.
After any compose-file change, a bare `podman-compose up -d` recreates every project container (stale config hash), including `db`, `db_pg18`, `rabbitmq` (30-minute stop grace each) and `valkey`.

### After the soak

Keep `/data/hpcperfstats_db/pg15` and the parked `db` container until the soak completes; deleting them is manual, never automatic.
`./scripts/rebuild_full_site.sh` keeps working after cutover: once the overlay is included it requires `db_pg18` instead of `db`.
You can delete the state file `/hpcperfstats/pg18_migrate/state.json` after cutover.

## Troubleshooting

- `copies_in_flight` stays at `1` while `--workers` is higher: check PG18 for `Lock:relation` waits on `host_data`.
  That is the chunk-creation lock the script avoids, so an older script without chunk pre-creation is running.

- Phase B prints `value_types=` with `real` columns: that PG18 database predates the `double precision` schema and must be rebuilt from an empty database.
  This works only while PG15 is authoritative (before cutover).
  No migration retypes the columns: PG15 applied `0001` long ago and keeps `real`, and redeploying `web` does not change its schema.
  Stop the copy and any index restore, drop and recreate the PG18 database, and move the old state files aside; PG15, `web`, `pipeline` and the other containers stay untouched:

  ```bash
  K="hpcperfstats_pg18_copy hpcperfstats_pg18_indexes"
  podman stop -t 30 $K 2>/dev/null
  podman rm -f $K 2>/dev/null
  P="podman-compose -p hpcperfstats --profile pg18-migrate"
  D="DROP DATABASE IF EXISTS hpcperfstats WITH (FORCE)"
  C="CREATE DATABASE hpcperfstats OWNER hpcperfstats"
  $P exec -T db_pg18 psql -h localhost -U hpcperfstats -d postgres \
    -XtA -v ON_ERROR_STOP=1 -c "$D" -c "$C"
  M='cd /hpcperfstats/pg18_migrate && for f in state*.json; do
    [ -e "$f" ] && mv "$f" "$f.pre-double"; done; ls'
  $P run --rm --no-deps -T --entrypoint sh web -c "$M"
  ```

  Then run Phase B, Phase C and the rest of this page as for a first migration.
  The `*.pre-double` files are only a record; nothing reads them.

## Related

- [Compose services: db image](../../reference/compose-services.md#db-image)
- [Upgrade the stack](upgrade-the-stack.md#upgrade-the-timescale-catalog-on-postgresql-15)
- `docker-compose.pg18-primary.yaml`, `scripts/pg18_host_data_chunk_copy.py`, `scripts/pg18_relational_copy.sh`
- `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`
