# Operator guide: PostgreSQL 15 → homemade PG18 + Timescale logical migrate

**Audience:** site operators cutting over from Hub `timescale/timescaledb:2.28.3-pg15` to the homemade `hpcperfstats-db` image (PostgreSQL 18 + TimescaleDB 2.29.x on Alpine 3.24 musl with jemalloc, ICU, liburing, lz4, zstd).

**Do not use `pg_upgrade`.** Timescale 2.29 drops PG15; do **not** restore `_timescaledb_catalog` from PG15. Create a fresh hypertable via Django migrations on PG18, then **COPY rows**.

Contract rule: `hpcperfstats/cursor-rules/postgres-custom-image-and-migrate-contract.mdc`.

Run compose from the checkout that contains `docker-compose.yaml` (typically `HPCPerfStats/`). Do not prefix paste blocks with host `cd`.

---

## Prerequisites

1. Host free disk ≈ **2×** current `host_data` size (dual volumes until PG15 retirement).
2. Settings include `postgres_data_pg18` bind (`/data/hpcperfstats_db/pg18` in the example).
3. Create the host directory and give it to Alpine **postgres uid/gid 70** before starting `db_pg18`. A bind mount **replaces** the image’s `1777` ownership; empty `root:root` (or NFS `root_squash`) yields `mkdir: can't create directory '/var/lib/postgresql/18/': Permission denied`.

```bash
sudo mkdir -p /data/hpcperfstats_db/pg18
sudo chown -R 70:70 /data/hpcperfstats_db/pg18
```

Then restart:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate up -d db_pg18
```

4. Build on the **production CPU** (`-march=native`). Do not ship an ARM/Colima bake to x86 prod.

---

## Phase A — Build and start PG18 beside PG15

Hub `db` (alias `db`) stays the live writer. PG18 uses profile `pg18-migrate` and alias `db18`.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate build db_pg18 && docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate up -d db_pg18
```

### db_pg18 — paste health + io_uring + Timescale

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'pg_isready -U hpcperfstats -h 127.0.0.1 && psql -h localhost -U hpcperfstats -c "SELECT version();" -c "SHOW shared_preload_libraries;" -c "SHOW io_method;" -c "SELECT default_version FROM pg_available_extensions WHERE name = '\''timescaledb'\'';"'
```

**Fail closed:** `SHOW io_method` must be `io_uring`. If not, check host `/proc/sys/kernel/io_uring_disabled` is `0` and that `db_pg18` still has `security_opt: seccomp=unconfined` (Docker default seccomp blocks io_uring).

---

## Phase B — Empty schema on PG18 (Django migrate)

`db_pg18` must already be healthy (Phase A). Do **not** point the long-running **`web`** service at `db18` yet — Hub PG15 stays on alias **`db`**.

`machine.0001_initial` calls `create_hypertable` but does **not** `CREATE EXTENSION timescaledb`. Hub Timescale images often leave the extension in place from earlier ops; a fresh PG18 database only has `shared_preload_libraries = timescaledb`. **Create the extension first**, then one-shot migrate with Django `HOST=db18`, then disable compression for backfill.

### db_pg18 — CREATE EXTENSION timescaledb (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" -c "SELECT extname, extversion FROM pg_extension WHERE extname = '\''timescaledb'\'';" -c "SELECT proname FROM pg_proc WHERE proname = '\''create_hypertable'\'' LIMIT 3;"'
```

**Fail closed:** `extversion` is set (e.g. `2.29.2`) and `create_hypertable` appears in `pg_proc`.

### web — migrate empty schema onto db18 (paste output)

Requires profile **`pg18-migrate`** so hostname **`db18`** resolves. `--no-deps` assumes **`db_pg18`** is already up. Run **after** the extension paste above.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps --entrypoint /usr/local/bin/python3 web -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hpcperfstats.site.hpcperfstats_site.settings")
import django
django.setup()
from django.conf import settings
from django.db import connection, connections
connections.close_all()
settings.DATABASES["default"]["HOST"] = "db18"
connection.ensure_connection()
print("connected_host", settings.DATABASES["default"]["HOST"])
with connection.cursor() as cur:
    cur.execute("SELECT version()")
    print("server", cur.fetchone()[0])
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = %s", ["timescaledb"])
    row = cur.fetchone()
    print("timescaledb", row[0] if row else None)
    if not row:
        raise SystemExit("timescaledb extension missing — run Phase B CREATE EXTENSION paste first")
from django.core.management import call_command
call_command("migrate", verbosity=1)
print("migrate_ok")
'
```

**Fail closed:** printed `connected_host` is `db18`, `timescaledb` is non-empty, migrate finishes without error. If you see `create_hypertable … does not exist`, the extension paste was skipped or ran against the wrong database.

### db_pg18 — verify empty host_data hypertable + disable compression (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "SELECT version();" -c "SELECT extversion FROM pg_extension WHERE extname = '\''timescaledb'\'';" -c "SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables WHERE hypertable_name = '\''host_data'\'';" -c "SELECT count(*) AS host_data_n FROM host_data;" -c "SELECT remove_compression_policy('\''host_data'\'', if_exists => true);" -c "SELECT job_id, proc_name, scheduled FROM timescaledb_information.jobs WHERE hypertable_name = '\''host_data'\'' AND proc_name LIKE '\''%policy_compression%'\'';"'
```

**Fail closed:** `host_data` hypertable exists; `host_data_n` is **0**; compression policy job row is empty / removed. Re-enable compression only after Phase D verification (site change log should record the `add_compression_policy` you restore — typically `compress_after => INTERVAL '8d'` per migration `0023` / `0032`).

---

## Phase C — Live chunk copy (writers stay on PG15)

Watermark default: chunks with `range_end < now() - 3 days`. Ingest can still write into older ranges — this is **best-effort**, not a freeze.

From a host or container that can reach both `db` and `db18` with `psql` on PATH (and optional `zstd` for `--dump-dir`):

```bash
docker compose -p hpcperfstats -f docker-compose.yaml run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/HPCPerfStats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --list-only -v
```

(Adjust the script path to the bind-mounted checkout inside `web` if your layout differs.) Then omit `--list-only` to copy. Optional `--dump-dir /path` writes `chunk_*.pgcopy.zst` for resume/audit.

Per chunk the tool **deletes the target time range then COPY** so retries are safe. It never COPY's the empty parent `host_data`.

---

## Phase D — Freeze and final dump (fail-closed order)

1. Stop pipeline writers: `docker compose -p hpcperfstats -f docker-compose.yaml stop pipeline`
2. Stop web writers: `docker compose -p hpcperfstats -f docker-compose.yaml stop web`
3. Confirm no app backends on PG15 besides the copy session:

### db — paste pg_stat_activity

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec db psql -h localhost -U hpcperfstats -c "SELECT pid, usename, application_name, state, query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();"
```

4. Re-scan source chunks: any count mismatch vs target, or `range_end` in the hot window, or changed `n_live_tup` → **delete-range + COPY** again (raise `--watermark-days` to `0` or copy remaining chunks explicitly).
5. `pg_dump` **relational** tables from PG15 (`--no-owner`): `job_data`, `metrics_data`, `proc_data`, artifacts, Django tables. Restore into `db18`. **Forbidden:** `pg_dump -t host_data` alone (empty parent).
6. `ANALYZE` on target; optional re-enable compression policy.
7. Verification (all must pass):

### db + db_pg18 — paste verification counts

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db psql -h localhost -U hpcperfstats -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) FROM job_data;" -c "SELECT count(*) FROM metrics_data;"
```

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 psql -h localhost -U hpcperfstats -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) FROM job_data;" -c "SELECT count(*) FROM metrics_data;" -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';" -c "SHOW shared_preload_libraries;" -c "SHOW io_method;"
```

Also compare per-day counts for the last 14 days. Treat any `host_data` count mismatch after a complete freeze as **fail**.

---

## Phase E — Cutover and rollback

**Cutover:** move compose network alias `db` from the Hub PG15 service to `db_pg18` (or rename services), recreate **web** and **pipeline** so they resolve `db` to PG18. Leave the PG15 container **stopped** and its volume **intact** for the rollback window.

**Rollback:** stop web/pipeline; restore alias `db` to the PG15 service/volume; start stack. Do not delete `pg15` data until soak is complete.

After cutover soak, operators may archive/delete `/data/hpcperfstats_db/pg15` — **not automatic**.

---

## Image bake notes

| Item | Contract |
|------|----------|
| Base | `alpine:3.24.1` (not `latest`) |
| Postgres | 18.x SHA-pinned in `services-conf/db.Dockerfile` |
| Timescale | 2.29.x (not `APACHE_ONLY`; no external lz4/zstd DT_NEEDED on `timescaledb.so`) |
| `/opt` | jemalloc, **zlib-ng**, icu, liburing, lz4, zstd with rpath on **postgres** + zstd CLI (`HAVE_ZLIB=1` + `HAVE_LZ4=1`; no apk `zlib`) |
| CFLAGS (PG + Timescale) | `-O3 -march=native -mprefer-vector-width=512 -mtune=native -flto=auto -g0` |
| Volume | `/var/lib/postgresql` (PG18 layout) |
| Seccomp | `seccomp=unconfined` on `db_pg18` for `io_method=io_uring` |

---

## Related docs

- `README.md` Installation (mkdir `pg15` / `pg18`, dual-run note)
- `docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md` (stay on 2.28.x while on PG15)
- `docs/TESTING.md` (test overlay still uses Hub `db` until cutover)
- `scripts/pg18_host_data_chunk_copy.py`
