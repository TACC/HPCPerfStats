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

4. Host **io_uring** for `db_pg18` (`-c io_method=io_uring`). Postgres runs as Alpine **uid/gid 70**. Preferred locked-down compromise: **`kernel.io_uring_disabled=1`** plus **`kernel.io_uring_group=70`**. With `disabled=1` and the default unset group (`-1`), uid 70 gets **EPERM** even when compose has `seccomp=unconfined`. **Forbidden:** `kernel.io_uring_disabled=2`. Alternative (fully open): `kernel.io_uring_disabled=0`.

### host — apply io_uring sysctl now (paste output)

```bash
sudo sysctl -w kernel.io_uring_disabled=1
sudo sysctl -w kernel.io_uring_group=70
sysctl kernel.io_uring_disabled kernel.io_uring_group
```

**Fail closed:** printed values are `kernel.io_uring_disabled = 1` and `kernel.io_uring_group = 70`.

### host — persist io_uring sysctl across reboot (paste output)

```bash
printf '%s\n' 'kernel.io_uring_disabled = 1' 'kernel.io_uring_group = 70' | sudo tee /etc/sysctl.d/99-hpcperfstats-io-uring.conf
sudo sysctl --system
sysctl kernel.io_uring_disabled kernel.io_uring_group
```

Then start (or recreate) `db_pg18`:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate up -d --force-recreate db_pg18
```

5. Build on the **production CPU** (`-march=native`). Do not ship an ARM/Colima bake to x86 prod.

---

## Phase A — Build and start PG18 beside PG15

Hub `db` (alias `db`) stays the live writer. PG18 uses profile `pg18-migrate` and alias `db18`. Host io_uring sysctl from Prerequisites step 4 must already be applied.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate build db_pg18 && docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate up -d db_pg18
```

### db_pg18 — wait until accepting connections (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'pg_isready -U hpcperfstats -d postgres -h 127.0.0.1'
```

### db_pg18 — create database `hpcperfstats` if missing (paste output)

Init may have been skipped during an early crash loop even when compose sets `POSTGRES_DB=hpcperfstats`. Always run this against the maintenance DB **`postgres`** before Phase B migrate. Idempotent if the DB already exists.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'psql -h localhost -U hpcperfstats -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '\''hpcperfstats'\''" | grep -q 1 || psql -h localhost -U hpcperfstats -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE hpcperfstats OWNER hpcperfstats;" ; psql -h localhost -U hpcperfstats -d postgres -c "SELECT datname FROM pg_database WHERE datname = '\''hpcperfstats'\'';"'
```

**Fail closed:** printed `datname` is `hpcperfstats`.

### db_pg18 — paste health + io_uring + Timescale

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'pg_isready -U hpcperfstats -d hpcperfstats -h 127.0.0.1 && psql -h localhost -U hpcperfstats -d hpcperfstats -c "SELECT version();" -c "SHOW shared_preload_libraries;" -c "SHOW io_method;" -c "SELECT default_version FROM pg_available_extensions WHERE name = '\''timescaledb'\'';"'
```

**Fail closed:** `SHOW io_method` must be `io_uring`. If not: re-run Prerequisites host sysctl (`disabled=1` + `group=70`, or `disabled=0`); confirm `db_pg18` still has `security_opt: [seccomp=unconfined, label=disable]` and `cap_add: [SYS_ADMIN]`; then `up -d --force-recreate db_pg18`.

---

## Phase B — Empty schema on PG18 (Django migrate)

`db_pg18` must already be healthy (Phase A). Do **not** point the long-running **`web`** service at `db18` yet — Hub PG15 stays on alias **`db`**.

`machine.0001_initial` runs `CREATE EXTENSION IF NOT EXISTS timescaledb` before `create_hypertable` (required on fresh PG18: preload alone does not install the extension). Use a checkout / `web` image that includes that `0001` fix. One-shot migrate with Django `HOST=db18`, then disable compression for backfill.

### web — migrate empty schema onto db18 (paste output)

Requires profile **`pg18-migrate`** so hostname **`db18`** resolves. `--no-deps` assumes **`db_pg18`** is already up.

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
from django.core.management import call_command
call_command("migrate", verbosity=1)
print("migrate_ok")
'
```

**Fail closed:** printed `connected_host` is `db18`; migrate finishes without error. If you see `create_hypertable … does not exist`, the `web` image still has an old `0001` without `CREATE EXTENSION` — rebuild/sync the image, or once: `psql … -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"` then re-run migrate.

### db_pg18 — verify empty host_data hypertable + disable compression (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 sh -lc 'psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "SELECT version();" -c "SELECT extversion FROM pg_extension WHERE extname = '\''timescaledb'\'';" -c "SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables WHERE hypertable_name = '\''host_data'\'';" -c "SELECT count(*) AS host_data_n FROM host_data;" -c "SELECT remove_compression_policy('\''host_data'\'', if_exists => true);" -c "SELECT job_id, proc_name, scheduled FROM timescaledb_information.jobs WHERE hypertable_name = '\''host_data'\'' AND proc_name LIKE '\''%policy_compression%'\'';"'
```

**Fail closed:** `host_data` hypertable exists; `host_data_n` is **0**; compression policy job row is empty / removed. Re-enable compression only after Phase D verification (site change log should record the `add_compression_policy` you restore — typically `compress_after => INTERVAL '8d'` per migration `0023` / `0032`).

---

## Phase C — Live chunk copy (writers stay on PG15)

Watermark default: chunks with `range_end < now() - 3 days`. Ingest can still write into older ranges — this is **best-effort**, not a freeze. Requires profile **`pg18-migrate`** so `db18` resolves.

Script path inside the image is **`/home/hpcperfstats/scripts/…`** (Dockerfile `COPY . .` into `WORKDIR /home/hpcperfstats`). There is **no** nested `…/HPCPerfStats/scripts/` in the container. If `ls` fails after a rebuild, the file was missing from the **host** build context — confirm it exists next to `docker-compose.yaml` on the build host, then rebuild `web`.

The script uses **psycopg** (bundled in `web`); it does **not** need a `psql` binary on PATH. Set `-e PGPASSWORD=…` as in the paste blocks below.

### web — list chunks selected by default 3-day watermark (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --list-only -v
```

### web — copy those chunks (omit `--list-only`; paste progress / exit code)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 -v
```

Optional audit/resume files (path must be writable inside `web`):

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --dump-dir /tmp/pg18_chunk_dumps -v
```

Per chunk the tool **deletes the target time range then COPY** so retries are safe. It never COPY's the empty parent `host_data`.

---

## Phase D — Freeze and final dump (fail-closed order)

Writers must be stopped before the final host_data pass and relational dump. Do **not** cut alias `db` yet (that is Phase E). Run from the checkout that contains `docker-compose.yaml`.

### D1 — stop pipeline (paste exit / status)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml stop pipeline && docker compose -p hpcperfstats -f docker-compose.yaml ps pipeline
```

### D2 — stop web + proxy (paste exit / status)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml stop web proxy && docker compose -p hpcperfstats -f docker-compose.yaml ps web proxy
```

### D3 — db — confirm no app backends (paste `pg_stat_activity`)

**Fail closed:** only idle/`autovacuum`/your own `psql` rows — no `listend`, `sync_timedb`, gunicorn, or long `INSERT`/`COPY` from the app.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec db psql -h localhost -U hpcperfstats -d hpcperfstats -c "SELECT pid, usename, application_name, client_addr, state, left(query, 120) AS query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() ORDER BY pid;"
```

### D4 — web — list **all** chunks for freeze (`--watermark-days -7`) (paste output)

Negative days push the watermark into the future so open/hot 1-day chunks are included (`range_end < now() + 7 days`).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --watermark-days -7 --list-only -v
```

### D5 — web — freeze re-copy every selected chunk (paste progress / exit code)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate run --rm --no-deps -e PGPASSWORD=hpcperfstats web python3 /home/hpcperfstats/scripts/pg18_host_data_chunk_copy.py --source-host db --target-host db18 --watermark-days -7 -v
```

### D6 — db — source totals + last-14-day daily counts (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec db psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) AS job_data_n FROM job_data;" -c "SELECT count(*) AS metrics_data_n FROM metrics_data;" -c "SELECT count(*) AS proc_data_n FROM proc_data;" -c "SELECT date_trunc('day', time) AS day, count(*) AS n FROM host_data WHERE time >= now() - interval '14 days' GROUP BY 1 ORDER BY 1;"
```

### D7 — db_pg18 — target totals + last-14-day daily counts (paste output)

**Fail closed:** `host_data_n`, `host_data_max`, and each of the 14 daily `n` values must match D6. Mismatch → re-run **D5**, then D6/D7 again. Do **not** proceed to relational dump until host_data matches.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) AS job_data_n FROM job_data;" -c "SELECT count(*) AS metrics_data_n FROM metrics_data;" -c "SELECT count(*) AS proc_data_n FROM proc_data;" -c "SELECT date_trunc('day', time) AS day, count(*) AS n FROM host_data WHERE time >= now() - interval '14 days' GROUP BY 1 ORDER BY 1;"
```

### D8 — db_pg18 — truncate relational tables before restore (keep `host_data` + `django_migrations`)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "TRUNCATE TABLE job_data, metrics_data, proc_data, job_plot_artifact, job_detail_artifact, public_metrics_artifact, api_keys, test_login_user, xalt_run, xalt_object, xalt_link, join_run_object, join_link_object, auth_user, auth_group, auth_permission, auth_user_groups, auth_user_user_permissions, auth_group_permissions, django_content_type, django_session, django_site RESTART IDENTITY CASCADE;"
```

### D9 — host — `pg_dump` public data from PG15 → restore on PG18 (paste exit; **forbidden:** `-t host_data` alone)

Pipes Hub `pg_dump` into `db_pg18` `psql`. Excludes **`host_data`** (already copied) and **`django_migrations`** (already applied in Phase B).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec -T db pg_dump -h localhost -U hpcperfstats -d hpcperfstats --no-owner --no-acl --data-only --schema=public --exclude-table-data=host_data --exclude-table-data=django_migrations | docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec -T db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1
```

### D10 — db_pg18 — `ANALYZE` + re-enable compression policy (`compress_after` 8d)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 -c "ANALYZE;" -c "SELECT add_compression_policy('host_data', compress_after => INTERVAL '8d', if_not_exists => true);" -c "SELECT job_id, proc_name, schedule_interval, config FROM timescaledb_information.jobs WHERE hypertable_name = 'host_data' AND proc_name LIKE '%policy_compression%';"
```

### D11 — db — final verification counts (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec db psql -h localhost -U hpcperfstats -d hpcperfstats -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) AS job_data_n FROM job_data;" -c "SELECT count(*) AS metrics_data_n FROM metrics_data;" -c "SELECT count(*) AS proc_data_n FROM proc_data;" -c "SELECT count(*) AS job_plot_artifact_n FROM job_plot_artifact;" -c "SELECT count(*) AS api_keys_n FROM api_keys;"
```

### D12 — db_pg18 — final verification counts + Timescale/io_uring (paste output)

**Fail closed:** row counts in D11 and D12 match for every table listed; `io_method` is `io_uring`; `timescaledb` extension is present. Treat any `host_data` mismatch after freeze as **fail** — do not enter Phase E.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml --profile pg18-migrate exec db_pg18 psql -h localhost -U hpcperfstats -d hpcperfstats -c "SELECT count(*) AS host_data_n, max(time) AS host_data_max FROM host_data;" -c "SELECT count(*) AS job_data_n FROM job_data;" -c "SELECT count(*) AS metrics_data_n FROM metrics_data;" -c "SELECT count(*) AS proc_data_n FROM proc_data;" -c "SELECT count(*) AS job_plot_artifact_n FROM job_plot_artifact;" -c "SELECT count(*) AS api_keys_n FROM api_keys;" -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';" -c "SHOW shared_preload_libraries;" -c "SHOW io_method;"
```

---

## Phase E — Cutover and rollback

Leave the Hub PG15 **volume intact** for the rollback window. Do not delete `/data/hpcperfstats_db/pg15` until soak is complete.

### E1 — edit `docker-compose.yaml` on the host (not a compose command)

1. Service **`db`** (Hub PG15): change network alias `db` → `db15` (so the name `db` is free).
2. Service **`db_pg18`**: set aliases to include **`db`** (keep `db18` if you want); **remove** `profiles: [pg18-migrate]` so PG18 starts with the normal stack.
3. Save the file.

### E2 — stop Hub PG15 (volume stays; paste status)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml stop db && docker compose -p hpcperfstats -f docker-compose.yaml ps db
```

### E3 — start PG18 as hostname `db` (paste status)

After E1, the service may still be named `db_pg18` but must own alias **`db`**:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml up -d db_pg18 && docker compose -p hpcperfstats -f docker-compose.yaml ps db_pg18
```

### E4 — start app stack against alias `db` (paste status)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml up -d redis rabbitmq web pipeline proxy && docker compose -p hpcperfstats -f docker-compose.yaml ps
```

### E5 — web — confirm Django talks to PG18 (paste output)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec web /usr/local/bin/python3 -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hpcperfstats.site.hpcperfstats_site.settings")
import django
django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT version(), current_setting('\''io_method'\'', true)")
    print(cur.fetchone())
    cur.execute("SELECT count(*) FROM host_data")
    print("host_data_n", cur.fetchone()[0])
'
```

**Fail closed:** `version()` shows PostgreSQL **18**; `host_data_n` matches Phase D.

### E6 — rollback (only if cutover fails)

1. Edit compose: put alias **`db`** back on Hub service **`db`**; remove **`db`** from `db_pg18` (restore `db18` + profile if desired).
2. Then:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml stop web pipeline proxy db_pg18
docker compose -p hpcperfstats -f docker-compose.yaml up -d db
docker compose -p hpcperfstats -f docker-compose.yaml up -d redis rabbitmq web pipeline proxy
```

After a successful soak, operators may archive/delete `/data/hpcperfstats_db/pg15` — **not automatic**.

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
| Seccomp | `seccomp=unconfined` + `label=disable` + `cap_add: SYS_ADMIN` on `db_pg18` for `io_method=io_uring` |
| Host io_uring | Preferred: `kernel.io_uring_disabled=1` + `kernel.io_uring_group=70`; forbidden: `disabled=2` |

---

## Related docs

- `README.md` Installation (mkdir `pg15` / `pg18`, dual-run note)
- `docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md` (stay on 2.28.x while on PG15)
- `docs/TESTING.md` (test overlay still uses Hub `db` until cutover)
- `scripts/pg18_host_data_chunk_copy.py`
