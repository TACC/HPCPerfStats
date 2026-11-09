# Upgrade an existing HPCPerfStats stack

Use this document when a site **already runs** the Compose stack (or an older layout of it). For a **new** host, follow **[README.md](../README.md)** Installation only.

Do **not** mix these procedures into a greenfield install. Fresh clones should `cp` examples, create empty bind directories, and `podman-compose -p hpcperfstats up --build -d` as documented in the README.

---

## Migrate an existing local runtime to rootless Podman

Stop the old stack without global prune. Install DNF-managed Podman,
`podman-compose -p hpcperfstats`, and Node 24; allocate non-overlapping `/etc/subuid` and
`/etc/subgid` ranges large enough to represent container uid **901860**. Create
and own `/data/user/$USER/{cache,tmp,tools}` and
`/data/podman/$USER/{storage,images,volumes,cache,tmp}`.

Move no active container storage in place. Configure rootless
`~/.config/containers/storage.conf` with `/data/podman/$USER/storage` graphroot
and `/data/podman/$USER/images` imagestore, and `containers.conf` with the
`/data` volume and image-copy temporary paths. Run `podman system migrate`, then
verify `podman info` before rebuilding. Configure npm, pip, XDG, Playwright, and
temporary paths below `/data/user/$USER`; remove old root-filesystem caches only
after the new paths are verified.

Rebuild with `podman-compose -p hpcperfstats up --build -d`. Development uses
project `hpcperfstats-dev` and ports 8080/8443/1514; tests use
`hpcperfstats-test`. Never run global Podman prune or enable an alternate
runtime-compatibility endpoint. OCI filenames (`.dockerignore`,
`docker-compose*.yaml`) and service bake recipes under
`services-conf/*.Dockerfile` (web/pipeline:
`services-conf/pipeline_web.Dockerfile` with context `./`) remain the
canonical layout.

---

## Pull a new image / code release

1. Stop app services deliberately. Compose **`stop_grace_period`** defaults to **30s** for **`web`** (`HPCPERFSTATS_WEB_STOP_GRACE`) and **480s** for **`pipeline`** (`HPCPERFSTATS_PIPELINE_STOP_GRACE`). **`scripts/rebuild_pipeline.sh`** uses **`HPCPERFSTATS_PIPELINE_STOP_TIMEOUT`** default **480** and **`HPCPERFSTATS_WEB_STOP_TIMEOUT`** default **30**, and calls **`supervisorctl shutdown`** before **`stop -t`**. A shorter pipeline override SIGKILLs mid-drain. Supervisord stops programs one at a time (drain **`stopwaitsecs=130`** each). Approximate solo budgets: listend ~**20s**, update_metrics ~**30–60s**, sync_timedb up to **120s**. Expected clean SIGTERM-driven exit **143**. If a drain program exceeds **`stopwaitsecs`**, pipeline stdout shows **`killing '…' with SIGKILL`** (see **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`**). **`rabbitmq-watcher`** and **`rsync_data`** use **`stopsignal=KILL`** on every stop.

2. Rebuild and recreate the app. A full **`podman-compose -p hpcperfstats up --build`** plus recreating **`web`** is the primary way to land SPA fixes: startup fingerprint heal syncs the new package frontend into **`staticfiles_data`**.

   | Task | Command |
   |------|---------|
   | Rebuild SPA in running stack (optional hot path; no pipeline restart) | `./scripts/rebuild_frontend.sh` |
   | Rebuild shared hpcperfstats image; down proxy; ``up -d web proxy pipeline`` (db/valkey/rabbitmq stay up; proxy image not rebuilt); does **not** set pipeline ``memory.high`` | `./scripts/rebuild_pipeline.sh` |
   | Full stack: ``podman build`` web/proxy/db_pg18 (ThinLTO + CPython ``--enable-optimizations``); compose down + ``up -d --no-build`` (default profile — the ``db_pg18`` container is built but **not** started); does **not** set pipeline ``memory.high`` | `./scripts/rebuild_full_site.sh` |
   | Detached recreate of web+pipeline only (no image build) | `./scripts/recreate_web_pipeline.sh` |
   | Rebuild just the app and keep persistent services running | `docker compose stop -t 30 web proxy && docker compose stop -t 480 pipeline && docker compose up --build -d web pipeline && docker compose start proxy` |

   **`./scripts/rebuild_pipeline.sh`** rebuilds **`hpcperfstats`**, preserves live SPA, takes **proxy** down (same image), then starts **web**, **proxy**, and **pipeline** through the shared Podman adapter. Leaves **db** / **valkey** / **rabbitmq** running. After Let's Encrypt renew or changing **`server=`** / TLS source path, run **`podman-compose -p hpcperfstats restart proxy`**.

   **Codon parse pyext:** the same image build runs Dockerfile stage **`codon-build`** (in-image `hpcperfstats/dbload/codon/build_codon_ext.sh`) and ships `/opt/hpcperfstats/codon_parse` for `sync_timedb` (`PYTHONPATH` in supervisord). After cutover, confirm `codon_parse status=enabled` in pipeline/`sync_timedb` logs (see **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`** → *Codon parse bridge*). Do **not** host-prebuild or stage `.so` files into the build context.

3. SPA rebuilds and image builds bake the running git SHA into the staff actions menu (`SITE_GIT_COMMIT`). Image builds copy context `.git` into `frontend-builder` for `git rev-parse` (then strip `.git` from the runtime image after `COPY . .`). Optional `HPCPERFSTATS_GIT_COMMIT` build-arg / env still overrides when set. SPA-only **`./scripts/rebuild_frontend.sh`** exports the host SHA the same way.

4. After `collectstatic --noinput --clear`, startup verifies SPA shells under **`STATIC_ROOT/frontend/{machine,pub}/index.html`** and compares a **sha256 fingerprint** of package vs volume `machine/index.html`. `--clear` drops unused hashed files (and stale sidecars) from the persistent **`staticfiles_data`** staging volume before the new tree is copied. If shells are missing (for example a Vite-era volume after upgrading to the Next export), or fingerprints **differ** after a from-scratch image rebuild while **`staticfiles_data`** still holds an older Next tree, startup **auto-heals** by replacing `STATIC_ROOT/frontend` from package static. After heal and sidecar compress, startup **publishes** disk `STATIC_ROOT` and `MEDIA_ROOT` onto tmpfs **`staticfiles_ram`** / **`media_ram`** (nginx `/srv/static` and `/srv/media`). Empty media is a successful publish; tmpfs is empty after host reboot until web finishes that step. Image build `collectstatic` alone cannot update the named volume (it masks the image layer). If the package image itself lacks the shells, web fail-closes — rebuild target **`hpcperfstats-full`** (primary) or run **`./scripts/rebuild_frontend.sh`** (SPA-only hot path; that script re-publishes **static** ram so proxy sees the new frontend without a web restart).

5. Pipelines started from images with the current **`supervisor_startup.sh`** run at **`oom_score_adj` +500** (host OOM kills ingest before Postgres). Verify after a rebuild: `podman top hpcperfstats_pipeline_1 hpid comm | sed -n 2,4p | while read p c; do echo "$c $(cat /proc/$p/oom_score_adj)"; done` (expect `500`). An older running pipeline can be set live as root: `for p in $(podman top hpcperfstats_pipeline_1 hpid | tail -n +2); do echo 500 > /proc/$p/oom_score_adj; done`.
6. After changing pipeline **`mem_limit`** / **`memswap_limit`** or **`HPCPERFSTATS_PIPELINE_STOP_GRACE`**, recreate the container (`docker compose up -d --force-recreate pipeline`) and verify **`memory.max`** inside the cgroup is numeric (not `max`). Neither **`rebuild_pipeline.sh`** nor **`rebuild_full_site.sh`** writes **`memory.high`** (both calls are commented out), so the pipeline cgroup keeps the default (`max`). The old ⅔-of-`memory.max` write throttled every new process once the container passed it: probes timed out and `podman exec` hung (hpcperfstats04 2026-11-02). A pipeline started by an older `rebuild_pipeline.sh` still has it; clear it from the host: `echo max > "$(dirname /sys/fs/cgroup$(awk -F: '$1=="0"{print $3}' /proc/$(podman inspect hpcperfstats_pipeline_1 --format '{{.State.Pid}}')/cgroup))/memory.high"`. Verify inside the pipeline container (unescaped loop — do not escape `$f` through podman-compose):

   ```bash
   podman-compose -p hpcperfstats exec pipeline bash -lc 'for f in memory.max memory.high memory.current; do echo $f; cat /sys/fs/cgroup/$f; done'
   ```

   After either rebuild, expect **`memory.high`** to stay **`max`**. Full-site rebuild builds the **`db_pg18`** image but does **not** start it (profile **`pg18-migrate`**; start it manually with `podman-compose --profile pg18-migrate up -d --no-build db_pg18`); host PG18 prereqs remain in **`docs/OPERATOR_PG18_MIGRATION.md`**.

   A successful **`./scripts/rebuild_full_site.sh`** (no flags) ends with **`Stack summary`**: compose network id, **`podman-compose ps -a`**, and per-service container/image ids. If the log stops at **`Successfully tagged localhost/hpcperfstats:latest`** right after Dockerfile **`collectstatic`**, with no **`=== starting podman-compose up`** banner, the script **failed during the build phase** (commonly **`proxy`** or **`db_pg18`** while **`web`** already tagged) — scroll above the trap line for **`Error`** / **`BUILD FAILED`**, or retry **`podman build -f services-conf/proxy.Dockerfile -t hpcperfstats-proxy .`** from the git checkout and paste the failure. Wrong command, **`--build-only`**, or an outdated script on the host are other causes.

---

## Compose Valkey service

`docker-compose.defaults.yaml` pins **Valkey 9.1** (`docker.io/valkey/valkey:9.1.3-alpine3.24`) and runs `valkey-server --appendonly no --maxmemory 16gb --maxmemory-policy volatile-lru --io-threads 4 --unixsocket /run/valkey/valkey.sock --unixsocketperm 777`. The command must stay `valkey-server`: the image entrypoint drops root only for that name. `services-conf/valkey_entrypoint.sh` creates **`/run/valkey`** on the **`valkey_runtime`** named volume, which **`valkey`**, **`web`** and **`pipeline`** all mount, so the cache URL is **`unix:///run/valkey/valkey.sock?db=1`** (TCP **6379** remains for `valkey-cli` and non-compose clients). Do **not** put `valkey` on **`web`** / **`pipeline`** `depends_on` (podman-compose **`condition:`** leaves the container created with empty logs). Startup wait (`valkeywait.py`) remaps hostname **`valkey`** to the Unix socket URL, since that hostname is missing until the container joins the network. Do **not** use **`allkeys-*`** for Django/listend cache keys. `sync_timedb` stores no queues or member maps in Valkey; durable ingest/append state is `.sync_timedb_job_store.json` plus `.sync_timedb_archive_members/`. Valkey has no persistence volume (`appendonly no`), so every recreate clears cached pages/plots until they are recomputed. Size the Linux Podman host so Valkey can use the 16 GiB cap alongside Postgres `shm_size` / `shared_buffers`.

The app talks to Valkey through **valkey-py** (`valkey==6.2.0`) and **django-valkey** (`django-valkey==0.4.1`, backend `django_valkey.cache.ValkeyCache`, default pickle serializer). Both are pure-Python wheels; the optional `libvalkey` C parser extra is intentionally not installed, so the free-threaded pipeline interpreter keeps the GIL disabled. Wheel sha256 for audit: django-valkey `50509c60be59f61f6094720c3e326a22f54c6a3537e4729b9c63aa2e83c69e0b`, valkey `94a12c87cd070e356b2c89e2946fa582d9f65ec2e003ab1867e987220e2beae8`.

Bump the image tag on a running host:

```bash
podman-compose -p hpcperfstats pull valkey
podman-compose -p hpcperfstats up -d valkey
podman-compose -p hpcperfstats exec valkey valkey-cli INFO server | grep -E '^(server_name|valkey_version):'
podman-compose -p hpcperfstats restart web pipeline
```

Expect `server_name:valkey` and `valkey_version:9.1.x` (or the pin in compose). Roll back by restoring the previous image tag, then `pull` / `up -d valkey` and restart **web** and **pipeline**. If **`[CACHE] valkey_location`** points at an **external** server (not the Compose service), upgrade that server separately, then restart the app services that use the cache.

### Redis → Valkey cutover (one-time)

This release replaces the Compose `redis` service (Redis Open Source 8.10) with `valkey` and renames every project-owned name: service, container `hpcperfstats_valkey_1`, volume `valkey_runtime`, socket `/run/valkey/valkey.sock`, and INI key **`[CACHE] valkey_location`**. `conf_parser` does not read the old **`[CACHE] redis_location`** key, so the site ini must be migrated by the rewrite script (below). On the Python side, redis-py and Django's built-in `RedisCache` are replaced by valkey-py 6.2.0 + django-valkey 0.4.1 (sha256 above); the image rebuild installs them. The settings env override **`JOB_PLOT_REDIS_MAX_BYTES`** is now **`JOB_PLOT_VALKEY_MAX_BYTES`** (rename it wherever a host sets it).

From the checkout holding the site ini, after `git pull` to this release:

```bash
./scripts/rewrite_hpcperfstats_ini.sh --dry-run
./scripts/rewrite_hpcperfstats_ini.sh
./scripts/rebuild_full_site.sh
podman rm -f hpcperfstats_redis_1
podman volume rm hpcperfstats_redis_runtime
```

The dry run must print `renamed: [CACHE] redis_location -> valkey_location`, or nothing for that key if the site never set it. If it prints nothing while the site ini does set `redis_location`, the checkout was not pulled to the release. `rebuild_full_site.sh` bakes the rewritten ini, then runs `down --remove-orphans` and `up -d`, so `valkey`, `web` and `pipeline` start together on the new socket. The last two lines are cleanup only: nothing mounts the old container or volume any more. The cache starts empty (there was no persistence), as after any restart: job plots fall back to the DB artifact and `recent_host:*` refills within the listend TTL.

---

## PostgreSQL over the shared Unix socket

`web` and `pipeline` now connect to the PG15 `db` service through a Unix socket on the **`postgres_runtime`** tmpfs named volume (`/run/postgresql`), not TCP. TCP **5432** stays published on `hpcperfstats_net` for everything else. `db` also loads **`services-conf/postgres_pg_hba.conf`** through `-c hba_file=`, so socket logins need the password (scram-sha-256, same as network logins) instead of the image default `trust`. That file overrides `$PGDATA/pg_hba.conf` on existing data dirs. Loopback (`exec db psql -h localhost`) keeps `trust`; `exec db psql` without `-h` now uses the socket and fails with `fe_sendauth: no password supplied` (or a password prompt), so keep `-h localhost` on every in-container `psql` / `pg_dump`.

1. Make the site `hpcperfstats.ini` use the socket: **`/run/postgresql`** is now the code default for `[DEFAULT] host` (and `5432` for `port`, which names the socket file), so delete or comment out an old `host = db` line (or run `scripts/rewrite_hpcperfstats_ini.sh`, below).
2. Run **`./scripts/rebuild_full_site.sh`**. It bakes the new ini into the image, then runs `down --remove-orphans` and `up -d --no-build`, so **`db`**, **`web`**, and **`pipeline`** are recreated together and all mount `postgres_runtime`. After `up` it fails the rebuild if `web` or `pipeline` cannot see `/run/postgresql/.s.PGSQL.5432` within `HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT` (default 300 s). Do **not** recreate only `web`/`pipeline`: with the new ini and an old `db` they wait forever for a socket that never appears, and still show as running.
3. Check: `podman-compose -p hpcperfstats exec -T db psql -h localhost -U hpcperfstats -Atc "SHOW hba_file; SELECT count(*) FROM pg_stat_activity WHERE client_addr IS NULL AND backend_type = 'client backend';"` returns `/etc/postgresql/pg_hba.conf` and a non-zero count of socket clients once web/pipeline are up.

To roll back, set `host = db` again; the socket volume and hba file are harmless with TCP clients.

## Rewrite a site ini onto the current example

`scripts/rewrite_hpcperfstats_ini.py` (run via `scripts/rewrite_hpcperfstats_ini.sh`) rebuilds the site `hpcperfstats.ini` from the current **`hpcperfstats.ini.example`**: every comment, section and new key comes from the example, and only the site's own settings are filled in. A site value equal to the `conf_parser` code default stays a commented `# key = default` line. A value that differs is written on an active `key = value` line directly under its commented default, so both are visible. Required keys (no code default) replace the example placeholder. Keys found in a legacy section (for example `host` under `[PORTAL]`) move to the example's section. Renamed keys are migrated first through the script's `RENAMED_OPTIONS` table and printed (`renamed: [CACHE] redis_location -> valkey_location`); the value moves only when the new key is absent. For that key the old compose socket `/run/redis/redis.sock` becomes `/run/valkey/valkey.sock`, host `redis` becomes `valkey`, and `redis://` / `rediss://` become `valkey://` / `valkeys://`; any other host, port, credentials and the db index are kept. If the site sets both keys, the new one wins and the old one is reported as dropped. Keys the example no longer documents are dropped and printed (`dropped (not in example): …`). Before writing, the script checks that every key's effective value (site value, else default) is unchanged and refuses to write if not. In place it keeps the old file as `hpcperfstats.ini.bak.<UTC timestamp>`. The ini's owner, group and mode are captured before anything is written and applied to the rewritten file (or `--output` file) and to the `.bak`; both are written at `0600` first so the secrets are never readable by others mid-write. The `owner=` it prints is the container's view (under rootless podman `0:0` is the host user who ran it).

The host has no project Python, so run it through **`scripts/rewrite_hpcperfstats_ini.sh`**: it starts a throwaway container from the built image (`localhost/hpcperfstats:latest`, `--network none`) with the checkout mounted at `/work` and runs the checkout's copy of the script with the image's Python. The code and defaults it compares against are therefore this checkout's (pull the release first); no running container is touched and no redeploy is needed beforehand. From the checkout holding the site ini:

```bash
./scripts/rewrite_hpcperfstats_ini.sh --dry-run
./scripts/rewrite_hpcperfstats_ini.sh
```

The ini argument defaults to `hpcperfstats.ini`; `--output PATH` writes elsewhere and leaves the ini untouched; `--image IMAGE` picks another built image. Paths are relative to the current directory, which must be inside the checkout. Then rebuild as usual (for example `./scripts/rebuild_full_site.sh`) to bake the rewritten ini into the image (immutable-image policy).

## INI layout (legacy sections)

**Upgrading from an older ini layout:** PostgreSQL keys moved from **`[PORTAL]`** to **`[DEFAULT]`**; ingest/archive/metrics keys moved from **`[DEFAULT]`** / **`[PORTAL]`** to **`[PIPELINE]`**. Existing deployments keep working via legacy section fallbacks in `conf_parser` until you migrate keys into the new sections. Compare production INI to **`hpcperfstats.ini.example`** on the next image bake (immutable-image policy: bake INI into the image; do not bind-mount a mutable INI over production).

### Process-tree roof: absolute MiB → cgroup percentage (breaking)

Remove **`sync_process_tree_rss_limit_mb`** and **`sync_process_tree_rss_exit_mb`** from baked site INI; they are **no longer read**. Add under **`[PIPELINE]`**:

| Old key | New key | 128g production default |
| --- | --- | --- |
| `sync_process_tree_rss_limit_mb=50000` | **`sync_process_tree_rss_limit_cgroup_pct=40`** | **40** → effective roof **52428** MiB, raw budget **≈52428** MiB (`/ PEAK 1.0`) |
| `sync_process_tree_rss_exit_mb=0` | **`sync_process_tree_rss_exit_cgroup_pct=0`** | **0** = hard exit off |

Effective MiB = ``(memory.max × pct // 100) // (1024×1024)`` at runtime. **`limit_cgroup_pct=0`** disables defer and the raw-byte admit gate (same as old **`limit_mb=0`**). Recompute pct on non-128g hosts: ``round(100 × desired_roof_mib / memory_max_mib)``. Telem prints **`rss_limit_cgroup_pct=`** plus effective **`rss_limit_mib=`**. See **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`** § OOM.

### File-cache admit: absolute MiB → cgroup percentage (breaking)

Remove **`sync_cgroup_admit_max_file_cache_mib`** from baked site INI; it is **no longer read**. Add under **`[PIPELINE]`**:

| Old key | New key | 128g production trial |
| --- | --- | --- |
| `sync_cgroup_admit_max_file_cache_mib=81920` (or any MiB) | **`sync_cgroup_admit_max_file_cache_cgroup_pct=60`** (shipped default **60**) | **60** → effective cap **≈78643** MiB on 128g; **`0`** = gate off |

Effective MiB = ``process_memory.effective_cgroup_admit_max_file_cache_mib()`` (same formula as roof pct). Telem prints **`file_cache_cgroup_pct=`** plus effective **`file_cache_cfg_mib=`**. Tune **`file_cache_cgroup_pct`** only on file-cache-heavy hosts (not a separate MiB INI).

**192g pipeline cgroup (`mem_limit` 192G):** at default **60%**, effective file-cache admit cap **≈117965 MiB** — parallel ingest at full pool width can still pin **`skip_cgroup_headroom`** when page cache exceeds that tripwire. Primary relief is **`sync_pipeline_drop_page_cache=yes`** (Linux **`posix_fadvise`** on ingest/archive paths; legacy **`archive_zstd_drop_page_cache`** when the new key is omitted) plus P0 restart after deploy — **not** lowering **`sync_ingest_pool_processes`**. See **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`** § pipeline memory.

---

## Cluster syslog volume layout

If you previously used a separate host path for node logs (for example `/opt/hpcperfstats_log` mounted at `/hpcperfstatslog/`), copy any `cluster.log` into **`/data/hpcperfstats_data/site_data/logs/current/`** (or your edited `hpcperfstatsdata` device) if you need the history, then drop the extra compose volume. If you still have a local **`docker-compose.app.yaml`**, delete it — the app overlay is obsolete; use settings + base compose only.

---

## RabbitMQ queue type and memory (existing brokers)

Compose mounts `services-conf/rabbitmq_default_queue_type.conf` (`default_queue_type = quorum`). Classic queues OOM under thousands of monitor publisher connections. **New** durable monitor ingest queues are declared quorum; listend **passive-attaches** to an existing queue of any type (do not 406 `x-queue-type` against classic `stampede3`). Recreate the `rabbitmq` service after changing that conf file; **existing queues keep their declared type** — do **not** delete/recreate `stampede3` (or other `rmq_queue`) as classic to clear listend `INTERNAL_ERROR` 541 “timed out consuming from quorum queue” (that is Ra consume-setup / listend cancel churn; reconnect with backoff).

Compose **`mem_limit` / `memswap_limit` 96g** plus `services-conf/rabbitmq_vm_memory.conf` (`vm_memory_high_watermark.absolute = 64GiB` headroom). Compose **`ERL_FLAGS=+MBas aobf +MBlmbcs 512 +MHlmbcs 512`** tunes Erlang binary/heap carriers (recreate after changing that env). Console logging is **`warning`** with connection/channel **`error`** (`services-conf/rabbitmq_logging.conf` — not **`info`**). Crash dumps off: **`ERL_CRASH_DUMP_SECONDS=0`** + **`ulimits.core: 0`**. Recreate `rabbitmq` after changing mem limits, watermark, **`ERL_FLAGS`**, logging, crash-dump knobs, or **`services-conf/rabbitmq_frame_max.conf`** (`frame_max = 131072`; `docker compose up -d --force-recreate rabbitmq pipeline`). On hosts with less than 96 GiB RAM, lower **both** `mem_limit`/`memswap_limit` and the absolute watermark together. Inspect: `docker compose exec rabbitmq rabbitmqctl status` (Alarms + watermark; do **not** use `rabbitmqctl list_alarms` — absent on 4.3.x). Preserve/extract recovery: **[OPERATOR_RABBITMQ_RECOVERY.md](OPERATOR_RABBITMQ_RECOVERY.md)**.

---

## PostgreSQL 18 dual-run migrate (optional)

Compose keeps Hub **`timescale/timescaledb:2.28.3-pg15`** as hostname **`db`**. Homemade Alpine PG18 + Timescale (`services-conf/db.Dockerfile`, image `hpcperfstats-db`) is service **`db_pg18`** under profile **`pg18-migrate`** (alias **`db18`**, volume **`postgres_data_pg18`**). Logical chunk copy + freeze cutover: **`docs/OPERATOR_PG18_MIGRATION.md`**. Do **not** use `pg_upgrade`. Bake the DB image on the production CPU (`-march=native`). Do **not** change **`db`** or dual-run **`db_pg18`** `shm_size: "16gb"`.

**Checkpoint / WAL GUC updates** (bgwriter, `wal_compression`, `max_wal_size`, `checkpoint_timeout`, etc.) ship in **`docker-compose.yaml`** `command:` on both **`db`** and **`db_pg18`**. Recreate the affected service so Postgres reloads startup GUCs — e.g. **`podman-compose -p hpcperfstats -f docker-compose.yaml up -d --force-recreate db`**. Both PG15 and PG18 use **`wal_compression=lz4`**. Archive-heavy sites: confirm **`df`** on the PG bind (e.g. `/data/hpcperfstats_db/pg15`) separately from **`/data/hpcperfstats_data`** before raising WAL caps.

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
