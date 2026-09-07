# Upgrade an existing HPCPerfStats stack

Use this document when a site **already runs** the Compose stack (or an older layout of it). For a **new** host, follow **[README.md](../README.md)** Installation only.

Do **not** mix these procedures into a greenfield install. Fresh clones should `cp` examples, create empty bind directories, and `docker compose up --build -d` as documented in the README.

---

## Pull a new image / code release

1. Stop app services with enough grace for pipeline drain. Prefer `docker compose stop -t 180 pipeline` (or rebuild’s default **300s** via `HPCPERFSTATS_PIPELINE_STOP_TIMEOUT`) so wall clock exceeds sync_timedb’s **`SHUTDOWN_DRAIN_TIMEOUT_S` (120s)**. Keep compose **`stop_grace_period` ≥ 2m**. In **`services-conf/supervisord.conf`**, the three Python programs set **`stopwaitsecs=130`**. Approximate solo budgets: listend ~**20s**, update_metrics ~**30–60s**, sync_timedb up to **120s**. Expected SIGTERM-driven exit **143** (see **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`**). Do **not** `docker kill` / SIGKILL unless wedged past grace.

2. Rebuild and recreate the app. A full **`docker compose up --build`** (or equivalent from-scratch image rebuild) plus recreating **`web`** is the primary way to land SPA fixes: startup fingerprint heal syncs the new package frontend into **`staticfiles_data`**.

   | Task | Command |
   |------|---------|
   | Rebuild SPA in running stack (optional hot path; no pipeline restart) | `./scripts/rebuild_frontend.sh` |
   | Rebuild web/pipeline image after Python-only changes (preserves live frontend, no npm) | `./scripts/rebuild_pipeline.sh` |
   | Temporary pipeline-only rebuild (no running web; recreate pipeline only) | `./scripts/rebuild_pipeline.sh --no-web` |
   | Rebuild just the app and keep persistent services running | `docker compose stop -t 120 web pipeline proxy && docker compose up --build -d web pipeline && docker compose start proxy` |

   **`./scripts/rebuild_pipeline.sh`** does **not** restart **`proxy`**. After Let's Encrypt renew or changing **`server=`** / TLS source path, **`docker compose restart proxy`**.

3. SPA rebuilds and image builds bake the running git SHA into the staff actions menu (`SITE_GIT_COMMIT`). Image builds copy context `.git` into `frontend-builder` for `git rev-parse` (then strip `.git` from the runtime image after `COPY . .`). Optional `HPCPERFSTATS_GIT_COMMIT` build-arg / env still overrides when set. SPA-only **`./scripts/rebuild_frontend.sh`** exports the host SHA the same way.

4. After `collectstatic`, startup verifies SPA shells under **`STATIC_ROOT/frontend/{machine,pub}/index.html`** and compares a **sha256 fingerprint** of package vs volume `machine/index.html`. If shells are missing (for example a Vite-era volume after upgrading to the Next export), or fingerprints **differ** after a from-scratch image rebuild while **`staticfiles_data`** still holds an older Next tree, startup **auto-heals** by replacing `STATIC_ROOT/frontend` from package static. Image build `collectstatic` alone cannot update the named volume (it masks the image layer). If the package image itself lacks the shells, web fail-closes — rebuild target **`hpcperfstats-full`** (primary) or run **`./scripts/rebuild_frontend.sh`** (SPA-only hot path).

5. After changing pipeline **`mem_limit`** / **`memswap_limit`** or **`HPCPERFSTATS_PIPELINE_STOP_GRACE`**, recreate the container (`docker compose up -d --force-recreate pipeline`) and verify **`memory.max`** inside the cgroup is numeric (not `max`).

---

## Compose Redis image

`docker-compose.yaml` pins **Redis Open Source 8.10** (`redis:8.10.0-alpine3.23`) with **`maxmemory 16gb`**, **`volatile-lru`** (Django cache keys keep TTL and remain evictable), **`--io-threads 4`** / **`--io-threads-do-reads yes`**, compact hashes (**`--hash-min-template-entries 1`**), and Unix socket **`unix:///run/redis/redis.sock?db=1`** on the **`redis_runtime`** named volume (TCP **6379** remains for `redis-cli` and non-compose Redis). **`web`** and **`pipeline`** wait for Redis **`service_healthy`** before starting; startup wait falls back to the Unix socket URL, not **`redis://redis:6379/1`** (that hostname is missing until Redis joins the network). Do **not** use **`allkeys-*`** for Django/listend cache keys. `sync_timedb` no longer stores queues or member maps in Redis; durable ingest/append state is `.sync_timedb_job_store.json` plus `.sync_timedb_archive_members/`. Redis has no persistence volume (`appendonly no`), so upgrading still clears cached pages/plots until they are recomputed. Size the host (or Colima) so Redis can use that cap alongside Postgres `shm_size` / `shared_buffers`. After pulling this Redis wiring, recreate **`redis`**, **`web`**, and **`pipeline`** so they all mount **`redis_runtime`**, and bake **`[CACHE] redis_location = unix:///run/redis/redis.sock?db=1`**. Keep **`redis://redis:6379/1`** only for an external Redis host.

On the deployment host:

```bash
docker compose pull redis
docker compose up -d redis
docker compose exec redis redis-cli INFO server | grep redis_version
docker compose restart web pipeline
```

Expect `redis_version:8.8.x` (or the pin in compose). Roll back by restoring the previous image tag in `docker-compose.yaml`, then `pull` / `up -d redis` and restart **web** and **pipeline**. If **`[CACHE] redis_location`** in `hpcperfstats.ini` points at an **external** Redis host (not the Compose service), upgrade that server separately per [Redis OSS standalone upgrade](https://redis.io/docs/latest/operate/oss_and_stack/install/upgrade/standalone/) (supported path: 7.x → 8.x), then restart app services that use the cache.

---

## INI layout (legacy sections)

**Upgrading from an older ini layout:** PostgreSQL keys moved from **`[PORTAL]`** to **`[DEFAULT]`**; ingest/archive/metrics keys moved from **`[DEFAULT]`** / **`[PORTAL]`** to **`[PIPELINE]`**. Existing deployments keep working via legacy section fallbacks in `conf_parser` until you migrate keys into the new sections. Compare production INI to **`hpcperfstats.ini.example`** on the next image bake (immutable-image policy: bake INI into the image; do not bind-mount a mutable INI over production).

---

## Cluster syslog volume layout

If you previously used a separate host path for node logs (for example `/opt/hpcperfstats_log` mounted at `/hpcperfstatslog/`), copy any `cluster.log` into **`/data/hpcperfstats_data/site_data/logs/current/`** (or your edited `hpcperfstatsdata` device) if you need the history, then drop the extra compose volume. If you still have a local **`docker-compose.app.yaml`**, delete it — the app overlay is obsolete; use settings + base compose only.

---

## RabbitMQ queue type and memory (existing brokers)

Compose mounts `services-conf/rabbitmq_default_queue_type.conf` (`default_queue_type = quorum`). Classic queues OOM under thousands of monitor publisher connections. **New** durable monitor ingest queues are declared quorum; listend **passive-attaches** to an existing queue of any type (do not 406 `x-queue-type` against classic `stampede3`). Recreate the `rabbitmq` service after changing that conf file; **existing queues keep their declared type** — do **not** delete/recreate `stampede3` (or other `rmq_queue`) as classic to clear listend `INTERNAL_ERROR` 541 “timed out consuming from quorum queue” (that is Ra consume-setup / listend cancel churn; reconnect with backoff).

Compose **`mem_limit` / `memswap_limit` 96g** plus `services-conf/rabbitmq_vm_memory.conf` (`vm_memory_high_watermark.absolute = 80GiB` headroom). Recreate `rabbitmq` after changing either (`docker compose up -d --force-recreate rabbitmq`). On hosts with less than 96 GiB RAM, lower **both** `mem_limit`/`memswap_limit` and the absolute watermark together. Inspect: `docker compose exec rabbitmq rabbitmqctl status` (Alarms + watermark; do **not** use `rabbitmqctl list_alarms` — absent on 4.3.x).

---

## PostgreSQL 18 dual-run migrate (optional)

Compose keeps Hub **`timescale/timescaledb:2.28.3-pg15`** as hostname **`db`**. Homemade Alpine PG18 + Timescale (`services-conf/db.Dockerfile`, image `hpcperfstats-db`) is service **`db_pg18`** under profile **`pg18-migrate`** (alias **`db18`**, volume **`postgres_data_pg18`**). Logical chunk copy + freeze cutover: **`docs/OPERATOR_PG18_MIGRATION.md`**. Do **not** use `pg_upgrade`. Bake the DB image on the production CPU (`-march=native`). Do **not** change **`db`** or dual-run **`db_pg18`** `shm_size: "16gb"`.

Create the PG18 bind (Alpine postgres uid/gid **70**) before starting the profile:

```bash
sudo mkdir -p /data/hpcperfstats_db/pg18 && sudo chown -R 70:70 /data/hpcperfstats_db/pg18
```

**Host io_uring for `db_pg18` (preferred: `disabled=1` + gid 70):** Postgres in the homemade image runs as Alpine **uid/gid 70**. Apply before starting profile `pg18-migrate`. Do **not** use `kernel.io_uring_disabled=2`. For **`io_method=io_uring`**, set host sysctl to the locked-down compromise **`kernel.io_uring_disabled=1`** and **`kernel.io_uring_group=70`**, or fully open with **`disabled=0`**; never **`disabled=2`**. Compose already sets **`security_opt: [seccomp=unconfined, label=disable]`** and **`cap_add: [SYS_ADMIN]`** on **`db_pg18`**.

```bash
sudo sysctl -w kernel.io_uring_disabled=1
sudo sysctl -w kernel.io_uring_group=70
printf '%s\n' 'kernel.io_uring_disabled = 1' 'kernel.io_uring_group = 70' | sudo tee /etc/sysctl.d/99-hpcperfstats-io-uring.conf
sudo sysctl --system
sysctl kernel.io_uring_disabled kernel.io_uring_group
```

Alternative (fully open): `sudo sysctl -w kernel.io_uring_disabled=0`.

Timescale catalog hops while still on PG15: **`docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md`**. Stay on the **2.28.x** line on PostgreSQL 15 — Timescale **2.29+ drops PG15**.

---

## Related docs

- Fresh install: **[README.md](../README.md)** Installation
- PG18 logical copy: **`docs/OPERATOR_PG18_MIGRATION.md`**
- Pool sizing / zstd / OOM: **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`**
- `host_data` uniqueness migrate: **`docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md`**
