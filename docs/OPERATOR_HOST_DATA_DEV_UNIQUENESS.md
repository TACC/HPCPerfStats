# Operator: host_data 5-column uniqueness (Phase 1 decompress)

Phase 1 of the multi-GPU `dev` uniqueness rollout:

1. Migration `0030` records Django state `unique_together = (time, host, type, event, dev)` and **removes** the `host_data` compression policy (no UNIQUE swap, no decompress inside migrate).
2. Operators decompress compressed chunks with the commands below.
3. After decompression, normalize hpcperfstats02 by dropping its redundant 4-column PK while retaining the equivalent 4-column UNIQUE, matching the other sites.
4. **Phase 2** (separate release, migration `0032`) replaces the live 4-column UNIQUE with one including `dev` and restores `compress_after => INTERVAL '8d'` — **only after** `compressed_chunks = 0` on that site.

Phase 1 does **not** fix multi-GPU individuation; the live 4-column UNIQUE still collapses rows until Phase 2.

**Compose cwd (prose only):** run from the checkout containing `docker-compose.yaml` (typically `HPCPerfStats/`). Do not prefix paste blocks with `cd`.

## Per-site batch sizes (measured 2026-07-31)

| Host | Compressed chunks | Expansion | Suggested `LIMIT` | Notes |
|------|-------------------|-----------|-------------------|-------|
| hpcperfstats04 | 1 | ~0 | 5 | Trivial |
| hpcperfstats01 | 47 | ~409 GB | 5 | Cheap; mostly already uncompressed |
| hpcperfstats03 | 106 | ~3523 GB | **2** | Tightest free-space cushion (~2.6T after); `df` every batch |
| hpcperfstats02 | 1275 | ~3754 GB | 20 | Longest loop; normalize its redundant 4-col PK after decompression |

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
