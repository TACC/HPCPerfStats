# Operator: host_data 5-column uniqueness (Phase 1 decompress)

Phase 1 of the multi-GPU `dev` uniqueness rollout:

1. Migration `0030` records Django state `unique_together = (time, host, type, event, dev)` and **removes** the `host_data` compression policy (no UNIQUE swap, no decompress inside migrate).
2. Operators decompress compressed chunks with the commands below.
3. **Phase 2** (separate release, migration `0032`) adds the live DB UNIQUE / PK rewrite including `dev` and restores `compress_after => INTERVAL '8d'` — **only after** `compressed_chunks = 0` on that site.

Phase 1 does **not** fix multi-GPU individuation; the live 4-column UNIQUE (and on some sites a 4-column PK) still collapses rows until Phase 2.

**Compose cwd (prose only):** run from the checkout containing `docker-compose.yaml` (typically `HPCPerfStats/`). Do not prefix paste blocks with `cd`.

## Per-site batch sizes (measured 2026-07-31)

| Host | Compressed chunks | Expansion | Suggested `LIMIT` | Notes |
|------|-------------------|-----------|-------------------|-------|
| hpcperfstats04 | 1 | ~0 | 5 | Trivial |
| hpcperfstats01 | 47 | ~409 GB | 5 | Cheap; mostly already uncompressed |
| hpcperfstats03 | 106 | ~3523 GB | **2** | Tightest free-space cushion (~2.6T after); `df` every batch |
| hpcperfstats02 | 1275 | ~3754 GB | 20 | Longest loop; Phase 2 must also rewrite 4-col PK |

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

When this prints `compressed_chunks = 0`, that site is ready for Phase 2 (`0032`): drop 4-col UNIQUE by discovered `conname`, rewrite 4-col PK where present (verified on hpcperfstats02), add `UNIQUE (time, host, type, event, dev)`, restore compression policy at 8 days.

## If you faked `0030` to stop a crash loop

`migrate machine 0030 --fake` skips policy removal. Un-fake before deploying Phase 1:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec web bash -lc 'python3 hpcperfstats/site/manage.py migrate machine 0029 --fake'
```

Then redeploy the Phase 1 image and let `migrate` apply `0030` for real.
