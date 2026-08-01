# Operator: host_data 5-column uniqueness (Phase 1 decompress)

Phase 1 of the multi-GPU `dev` uniqueness rollout:

1. Migration `0030` records Django state `unique_together = (time, host, type, event, dev)` and **removes** the `host_data` compression policy (no UNIQUE swap, no decompress inside migrate).
2. Operators decompress compressed chunks with the commands below.
3. After decompression, normalize hpcperfstats02 by dropping its redundant 4-column PK while retaining the equivalent 4-column UNIQUE, matching the other sites.
4. **Phase 2** (separate release, migration `0032`) replaces the live 4-column UNIQUE with one including `dev` and restores `compress_after => INTERVAL '8d'` — **only after** `compressed_chunks = 0` on that site.

Phase 1 does **not** fix multi-GPU individuation; the live 4-column UNIQUE still collapses rows until Phase 2.

**Compose cwd (prose only):** run from the checkout containing `docker-compose.yaml` (typically `HPCPerfStats/`). Do not prefix paste blocks with `cd`.

## Per-site batch sizes (measured 2026-07-31)


| Host           | Compressed chunks | Expansion | Suggested `LIMIT` | Notes                                                              |
| -------------- | ----------------- | --------- | ----------------- | ------------------------------------------------------------------ |
| hpcperfstats04 | 1                 | ~0        | 5                 | Trivial                                                            |
| hpcperfstats01 | 47                | ~409 GB   | 5                 | Cheap; mostly already uncompressed                                 |
| hpcperfstats03 | 106               | ~3523 GB  | **2**             | Tightest free-space cushion (~2.6T after); `df` every batch        |
| hpcperfstats02 | 1275              | ~3754 GB  | 20                | Longest loop; normalize its redundant 4-col PK after decompression |


Recommended order: **04 → 01 → 03 → 02**. Abort a site if free space drops below its remaining projected expansion.

Do **not** copy one `LIMIT` across sites: ~3 GB/chunk on 02 vs ~34 GB/chunk on 03.

## Progress check

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SELECT count(*) FILTER (WHERE is_compressed) AS compressed_chunks, count(*) AS chunks FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data';"
```

## Decompress one batch

Repeat until `compressed_chunks = 0`. Set `LIMIT` from the table above (example uses 5 for 04/01).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SET statement_timeout = 0; SELECT decompress_chunk(format('%I.%I', chunk_schema, chunk_name)::regclass, true) FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data' AND is_compressed ORDER BY range_start LIMIT 5;"
```

### Parallel decompress (multi-CPU)

A single `SELECT decompress_chunk(…) FROM … LIMIT N` runs **serially** inside one Postgres session. Prefer the parallel helper: keeps up to *N* decompressions in flight; when any one finishes it rechecks `compressed_chunks` and starts another immediately (sliding pool).

```bash
./scripts/decompress_host_data_chunks.sh 10
```

Pass concurrency as the first argument (default **10**). Suggested starts: **02 → 10–20**, **01/04 → 5–10**, **03 → 2** (disk cushion; each chunk expands ~34 GB). Abort if `df` free space drops below remaining projected expansion. Per-chunk failures are skipped for the rest of the run; the script aborts only on stall or when nothing left can be started.
## Disk watch

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db df -h /var/lib/postgresql/data
```

Run between batches. On **hpcperfstats03**, run every batch (mount is shared with non-Postgres archive data).

## Done gate (Phase 2 prerequisite)

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SELECT count(*) FILTER (WHERE is_compressed) AS compressed_chunks FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data';"
```

When this prints `compressed_chunks = 0`, sites 01, 03, and 04 are ready for Phase 2 (`0032`). On hpcperfstats02, run the normalization command below first.

## Normalize hpcperfstats02 to the fleet constraint shape

Run this **only on hpcperfstats02** and only after its done gate prints `compressed_chunks = 0`.

The guarded block drops the primary key only when it is exactly `(time, host, type, event)` **and** the equivalent 4-column UNIQUE is present. It is a no-op if no primary key remains and aborts on any unexpected constraint shape. `lock_timeout` prevents the metadata change from waiting indefinitely; if it times out, retry during a quieter period.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -v ON_ERROR_STOP=1 -c "SET lock_timeout = '30s'; DO \$\$ DECLARE pk_name text; pk_columns text[]; has_matching_unique boolean; BEGIN SELECT c.conname, ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum ORDER BY k.ordinality) INTO pk_name, pk_columns FROM pg_constraint c WHERE c.conrelid = 'public.host_data'::regclass AND c.contype = 'p'; IF pk_name IS NULL THEN RAISE NOTICE 'host_data already has no primary key'; RETURN; END IF; SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.host_data'::regclass AND c.contype = 'u' AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum ORDER BY k.ordinality) = ARRAY['time','host','type','event']::text[]) INTO has_matching_unique; IF pk_columns <> ARRAY['time','host','type','event']::text[] THEN RAISE EXCEPTION 'refusing to drop unexpected host_data PK % on columns %', pk_name, pk_columns; END IF; IF NOT has_matching_unique THEN RAISE EXCEPTION 'refusing to drop host_data PK: equivalent 4-column UNIQUE is absent'; END IF; EXECUTE format('ALTER TABLE public.host_data DROP CONSTRAINT %I', pk_name); RAISE NOTICE 'dropped redundant host_data PK %', pk_name; END \$\$;" -c "SELECT c.conname, c.contype, ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum ORDER BY k.ordinality) AS columns FROM pg_constraint c WHERE c.conrelid = 'public.host_data'::regclass AND c.contype IN ('p', 'u') ORDER BY c.contype, c.conname;"
```

The verification output must contain the 4-column UNIQUE and no `contype = p` row. After that, hpcperfstats02 has the same constraint shape as the other sites, and Phase 2 only needs to replace the 4-column UNIQUE by discovered `conname`, add `UNIQUE (time, host, type, event, dev)`, and restore the compression policy at 8 days.

## If you faked `0030` to stop a crash loop

`migrate machine 0030 --fake` skips policy removal. Un-fake before deploying Phase 1:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec web bash -lc 'python3 hpcperfstats/site/manage.py migrate machine 0029 --fake'
```

Then redeploy the Phase 1 image and let `migrate` apply `0030` for real.

---

## Stage 2 — apply 5-column UNIQUE and restore compression (`0032`)

Stage 2 ships in a **separate image** that includes migration `0032_host_data_unique_include_dev_db`. It replaces the live 4-column UNIQUE with `UNIQUE (time, host, type, event, dev)` and restores `add_compression_policy('host_data', compress_after => INTERVAL '8d')`.

**Do not run Stage 2 until** that site’s Stage 1 done gate is green (`compressed_chunks = 0`) and, on **hpcperfstats02**, the PK normalize block above has left only the 4-column UNIQUE (no `contype = p`). Migration `0032` does **not** drop or rewrite primary keys; if a multi-column PK remains, migrate soft-skips with a NOTICE and leaves uniqueness unchanged.

**Compose cwd (prose only):** same as Stage 1 — checkout with `docker-compose.yaml`. Do not prefix paste blocks with `cd`.

### Preflight (per site)

**db — confirm decompress gate still zero and constraint shape:**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SET statement_timeout = 0; SELECT count(*) FILTER (WHERE is_compressed) AS compressed_chunks FROM timescaledb_information.chunks WHERE hypertable_name = 'host_data';" -c "SELECT c.conname, c.contype, ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum ORDER BY k.ordinality) AS columns FROM pg_constraint c WHERE c.conrelid = 'public.host_data'::regclass AND c.contype IN ('p', 'u') ORDER BY c.contype, c.conname;" -c "SELECT count(*) AS null_dev_rows FROM host_data WHERE dev IS NULL;"
```

Expect: `compressed_chunks = 0`; one 4-column UNIQUE on `(time, host, type, event)`; **no** primary-key row (especially on 02); `null_dev_rows` ideally `0`. If `null_dev_rows` is huge, backfill before migrate (session timeout off):

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SET statement_timeout = 0; UPDATE host_data SET dev = '' WHERE dev IS NULL;"
```

### Deploy and one-shot migrate (avoid `web` crash-loop)

`ADD UNIQUE` on a large `host_data` can run for a long time. Under Compose `restart: always`, a timed-out or killed startup migrate crash-loops `web`. Prefer **stopped `web` + stopped `pipeline` + one-shot migrate**, with `**db` (and redis/rabbitmq) left up**.

Checkout must already contain the Stage 2 commit (migration `0032_host_data_unique_include_dev_db`). Do not start `web` until the one-shot migrate finishes.

**1. Stop writers / migrate-on-start (`pipeline` then `web`). Keep `db` up:**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml stop -t 300 pipeline && docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml stop -t 120 web
```

**2. Build the Stage 2 app image (`web` / `pipeline` share it). Do not `up` yet:**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml build web pipeline
```

Python-only sites can instead use `./scripts/rebuild_pipeline.sh --no-start` (stops + builds; skips compose up). Prefer that when SPA did not change; use the `compose build` block above when you are not using the helper script.

**3. One-shot migrate with `web` still down (uses the image from step 2):**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml run --rm --no-deps web bash -lc 'python3 hpcperfstats/site/manage.py migrate machine 0032'
```

**4. Bring app containers back (recreate on the new image):**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml up -d --force-recreate --no-deps web && docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml up -d --force-recreate --no-deps pipeline
```

If `proxy` was stopped for a name-reuse recreate on podman-compose, start it again after `web` is healthy: `docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml start proxy`.

If you accidentally start `web` before the gates pass, `0032` is written to **soft-skip** (NOTICE + success) when compressed chunks remain or a multi-column PK is still present — uniqueness will not change until you fix the gate and re-run migrate (one-shot again with `web`/`pipeline` stopped).

### Post-check

**db — 5-col UNIQUE present; compression policy/job restored:**

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec db psql -h localhost -U hpcperfstats -c "SELECT c.conname, c.contype, ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum ORDER BY k.ordinality) AS columns FROM pg_constraint c WHERE c.conrelid = 'public.host_data'::regclass AND c.contype IN ('p', 'u') ORDER BY c.contype, c.conname;" -c "SELECT job_id, proc_name, schedule_interval, config FROM timescaledb_information.jobs WHERE hypertable_name = 'host_data' AND proc_name LIKE '%compress%' ORDER BY job_id;"
```

Expect a UNIQUE on `(time, host, type, event, dev)` (name may be `host_data_time_host_type_event_dev_uniq`) and a compression policy/job for `host_data` at **8 days**. After the policy ages in, `compressed_chunks` may become non-zero again — that is expected; do not re-run Stage 1 decompress unless you intentionally remove the policy again.