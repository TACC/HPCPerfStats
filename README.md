# HPCPerfStats

**The package formerly known as TACC Stats**

A toolkit for monitoring resource usage on HPC systems at multiple levels of resolution.

---

## Overview

The **hpcperfstats** package is split into two parts:

| Component | Build system | Role |
|-----------|--------------|------|
| **monitor** | Autotools | Online data collection and transmission in production |
| **hpcperfstats** | Python setuptools | Data curation and analysis (off-cluster) |

### Documentation (`docs/`)

| Document | What it is for |
|----------|----------------|
| [**MONITOR_VARIABLES.md**](docs/MONITOR_VARIABLES.md) | **Canonical reference** for monitor-reported variables: names, types, units, and semantics. Use this instead of any legacy “attributes definition” doc. |
| [**DEPLOY_CONCURRENCY_AND_NUMA.md**](docs/DEPLOY_CONCURRENCY_AND_NUMA.md) | Thread/process limits vs PostgreSQL, **`effective_cores`**, optional Compose **`cpuset`** fragments via `scripts/apply_compose_cpu_pinning.py` (all services + NUMA overrides). |
| [**design-document.md**](docs/design-document.md) | As-built system design: architecture, data flow, components, contracts, and operations context. |
| [**using-the-website-as-a-researcher.md**](docs/using-the-website-as-a-researcher.md) | How to read the Django/React job UI—plots, metrics, and diagnostic themes—for HPC users and researchers. |
| [**TESTING.md**](docs/TESTING.md) | Test commands, CI, compose-backed workflows, Playwright/Vitest, and host vs container pytest notes. |
| [**OPERATOR_HOST_DATA_DEV_UNIQUENESS.md**](docs/OPERATOR_HOST_DATA_DEV_UNIQUENESS.md) | `host_data` 5-col uniqueness: Stage 1 decompress (+ 02 PK normalize) and Stage 2 one-shot migrate for `0032` / compress_after 8d. |

**Maintaining `MONITOR_VARIABLES.md`:** the catalog is generated and augmented by maintainer scripts in the same folder: [`regenerate_monitor_variables_catalog.py`](docs/regenerate_monitor_variables_catalog.py), [`augment_monitor_variables_diagnostics.py`](docs/augment_monitor_variables_diagnostics.py).

**REST API note:** `GET /api/jobs/{jid}/{type_name}/` (type detail) returns a Bokeh **`tplot_item`** (`json_item` payload) plus `stats_data` / `schema`. Legacy **`tscript`** / **`tdiv`** fields were removed; clients should embed only `tplot_item` via Bokeh `embed_item`.

**API contract changes (2026-06):**

- `GET /api/jobs/` with filters that match **zero** jobs now returns **HTTP 200** with `nj: 0`, an empty `job_list`, and a `filter_summary` object. Do not treat HTTP 404 as “no matches” for successful searches (404 is reserved for database/unavailable errors on this endpoint).
- `GET /api/search/` was **removed**; job-ID and host routing are handled in the React SPA. API clients should use `GET /api/jobs/?jid=…` (or open `/machine/job/{jid}/` in a browser) instead of the old search redirect endpoint.

Building and installing the `hpcperfstatsd-3.0-1.el9.x86_64.rpm` package (via `monitor/hpcperfstats.spec`) installs a **systemd** service `hpcperfstats`. This service runs a daemon with ~3% overhead on a single core at 1 Hz sampling; it is typically configured for **5-minute** intervals, with samples at job start and end. The daemon **hpcperfstatsd** sends data to a **RabbitMQ** server over the administrative network. RabbitMQ must be installed and running on the server to receive data.

The **hpcperfstats** container orchestration sets up a Django/PostgreSQL ingest and archival stack plus a RabbitMQ server to receive data from the monitor on the nodes.

---

## Installation

### Monitor subpackage

The monitor now uses a **static-bundle** build flow for packaging. The canonical path builds pinned static archives for `libev`, `rabbitmq-c`, and (on x86) `LIKWID`, then compiles `hpcperfstatsd` with `--enable-all-static`.

1. **Install RPM build prerequisites** (Rocky/EL-like systems):

   ```bash
   sudo dnf install \
     gcc gcc-c++ make autoconf automake libtool cmake pkgconfig \
     systemd-rpm-macros gzip tar curl perl gawk pciutils rdma-core-devel \
     rpm-build
   ```

   On aarch64, install one of:

   ```bash
   sudo dnf install datacenter-gpu-manager-4-devel
   # or
   sudo dnf install libdcgm-devel
   ```

2. **Prepare rpmbuild directories and source tarball** (from `HPCPerfStats/monitor`):

   ```bash
   ./scripts/prepare_rpmbuild_dirs.sh
   ```

   This script:
   - creates `monitor/rpmbuild/{SPECS,SOURCES,BUILD,RPMS,SRPMS,BUILDROOT}`
   - runs `scripts/build_static_bundle.sh --deps-only` into `monitor/rpmbuild/static-prefix`
   - runs `autoreconf -fi`, `./configure`, and `make dist`
   - copies `hpcperfstats-<version>.tar.gz` to `rpmbuild/SOURCES`

3. **Build the RPM**:

   Use the `rpmbuild` command printed by `scripts/prepare_rpmbuild_dirs.sh`. A typical script output is:

   ```bash
   rpmbuild -ba --define "_topdir $(pwd)/rpmbuild" rpmbuild/SPECS/hpcperfstats.spec
   ```

4. **Optional build options**:

   - Reuse existing static deps when already staged:
     ```bash
     SKIP_DEPS=1 ./scripts/prepare_rpmbuild_dirs.sh
     ```
   - Build dependencies + monitor binary directly (without rpmbuild staging):
     ```bash
     ./scripts/build_static_bundle.sh
     ```
   - Build only pinned dependency archives:
     ```bash
     ./scripts/build_static_bundle.sh --deps-only
     ```
   - Release-optimized monitor build:
     ```bash
     ./scripts/build_static_bundle.sh --release
     # equivalent to: HPC_BUNDLE_RELEASE_BUILD=1 ./scripts/build_static_bundle.sh
     ```
   - Pass extra configure args through bundle build (example):
     ```bash
     ./scripts/build_static_bundle.sh --disable-lustre
     ```

5. **Configuration** — after install, edit `/etc/hpcperfstats/hpcperfstats.conf`:

   | Field | Description |
   |-------|-------------|
   | `server` | Hostname or IP of the RabbitMQ server |
   | `queue` | System/cluster name being monitored |
   | `port` | RabbitMQ port (default `5672`) |
   | `freq` | Sampling interval in seconds |

   Example:

   ```ini
   server localhost
   queue default
   port 5672
   freq 600
   ```

   Reload a running daemon with: `kill -HUP <pid>` (or restart the service).

6. **Service control:**

   ```bash
   sudo systemctl start hpcperfstats
   sudo systemctl stop hpcperfstats
   sudo systemctl restart hpcperfstats
   ```

---

### Job scheduler configuration

**Job start/end:** Notify hpcperfstats by writing to `/var/run/stats_jobid` on each node:

- **Job start:** echo the job ID into the file  
- **Job end:** echo `-` into the file  

Do this from your scheduler’s **prolog** and **epilog**.

**Accounting ingest (SLURM `sacct`):** Use `hpcperfstats-sacct-gen` from the **hpcperfstats-tools** package. By default this command runs `sacct` for a date range and POSTs the results to the HPCPerfStats API ingest endpoint. Each successful POST also creates or overwrites a daily accounting file at `{acct_path}/YYYY-MM-DD.txt` (same pipe-delimited body Slurm returned). Alternatively, use **`-f DIR`** to write those `YYYY-MM-DD.txt` files locally (same format/naming) without calling the API; `-f` is mutually exclusive with `--api-key`, and `DIR` must already exist. The scheduled `sync_acct.py` job can reingest these files from disk. The API rejects payloads with fewer lines than the existing file for that date (HTTP 409) so a partial `sacct` export cannot replace a fuller one.

1. **Install the tools (Python):**

   ```bash
   # These tools are not published to PyPI, so install from GitHub.
   git clone https://github.com/TACC/HPCPerfStats-tools.git
   cd HPCPerfStats-tools
   python3 -m pip install .
   ```

2. **Configure the API base URL:**

   Set `HPCPERFSTATS_TOOLS_INI` to an INI file that contains `[API] base_url` (see `hpcperfstats-tools/hpcperfstats-tools.ini.example` in the repo for a template).

3. **Run the ingest (requires a staff-capable API key):**

   ```bash
   # Ingest today only (default date range is today .. today+1 day)
   hpcperfstats-sacct-gen --api-key YOUR_KEY

   # Ingest an explicit date range (end_date is exclusive)
   hpcperfstats-sacct-gen 2024-01-01 2024-01-08 --api-key YOUR_KEY

   # Write daily YYYY-MM-DD.txt files locally instead of POSTing (DIR must exist;
   # mutually exclusive with --api-key)
   hpcperfstats-sacct-gen -f /path/to/accounting 2024-01-01 2024-01-08
   ```

**Run-location and permissions requirements:**

- Run `hpcperfstats-sacct-gen` on a host where Slurm’s `sacct` binary exists and works (typically a Slurm login node).
- Run it as a user that has the correct Slurm permissions to query the relevant jobs/accounts via `sacct`.
- **API mode:** the API key you pass with `--api-key` must be staff-capable for the ingest endpoint; the `web` container must have the shared data volume mounted at `/hpcperfstats/` (same as `pipeline`) so the API can write under `acct_path` (default `/hpcperfstats/accounting`).
- **File mode (`-f DIR`):** `DIR` must already exist; no API URL or key is required. Place or sync the resulting `.txt` files where `sync_acct.py` expects them (`acct_path`).

---

### hpcperfstats subpackage (container stack)

This is a container orchestration with Django/PostgreSQL, ingest/archival tools, and RabbitMQ. The steps below assume a **Rocky Linux** host.

1. **Install Docker/Podman:**

   ```bash
   sudo dnf install docker git podman-compose
   ```

   **Redis / Linux kernel:** The Compose stack runs Redis. Redis warns when **`vm.overcommit_memory`** is disabled; background RDB saves use **`fork()`**, and without overcommit the kernel can reject that fork even when RAM is sufficient. On the **Linux machine whose kernel runs the Redis container** (your Docker host on Rocky/Linux; **not** macOS `sysctl` when using Docker Desktop), enable overcommit:

   ```bash
   sudo sysctl -w vm.overcommit_memory=1
   ```

   Persist across reboots:

   ```bash
   echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-redis-overcommit.conf
   sudo sysctl --system
   ```

   Alternatively, add **`vm.overcommit_memory = 1`** to **`/etc/sysctl.conf`** and reboot (or run the **`sysctl -w`** command once). On Docker Desktop for macOS, host **`sysctl`** does not apply to the inner Linux VM; tune there only if your setup exposes it, or treat the warning as informational for small dev stacks.

   **Upgrading the Compose Redis server:** `docker-compose.yaml` pins **Redis Open Source 8.10** (`redis:8.10.0-alpine3.23`) with **`maxmemory 16gb`**, **`allkeys-lru`**, and **`--io-threads 4`** / **`--io-threads-do-reads yes`**. Redis holds only **ephemeral cache** (no persistence volume; `appendonly no`), so upgrading clears cached pages/plots until they are recomputed. Size the host (or Colima) so Redis can use that cap alongside Postgres `shm_size` / `shared_buffers`. On the deployment host:

   ```bash
   docker compose pull redis
   docker compose up -d redis
   docker compose exec redis redis-cli INFO server | grep redis_version
   docker compose restart web pipeline
   ```

   Expect `redis_version:8.8.x`. Roll back by restoring the previous image tag in `docker-compose.yaml`, then `pull` / `up -d redis` and restart **web** and **pipeline**. If **`[CACHE] redis_location`** in `hpcperfstats.ini` points at an **external** Redis host (not the Compose service), upgrade that server separately per [Redis OSS standalone upgrade](https://redis.io/docs/latest/operate/oss_and_stack/install/upgrade/standalone/) (supported path: 7.x → 8.x), then restart app services that use the cache.

2. **Enable container restart after reboot:**

   ```bash
   sudo systemctl enable podman-restart.service
   sudo systemctl start podman-restart.service
   ```

3. **Clone the repo:**

   ```bash
   git clone https://github.com/TACC/hpcperfstats.git
   cd hpcperfstats
   ```

4. **Compose file:**

   ```bash
   cp docker-compose.app.yaml.example docker-compose.app.yaml
   ```

   **`docker-compose.app.yaml` is gitignored** — treat **`docker-compose.app.yaml.example`** as the committed operator template. After `cp`, edit only site-specific paths below; any new ports, grace periods, memory caps, or volume wiring must be added to **`.example`** in the repo (see **`hpcperfstats/cursor-rules/docker-compose-app-example-sync.mdc`**) so the next clone gets them.

   Edit `docker-compose.app.yaml` and set:

   - **pipeline → volumes:** path to a `.ssh` directory with valid keys and permissions  
   - **volumes → hpcperfstatsdata → device:** path for data (your user and directory)  

   Create the directories (e.g.):

   ```bash
   sudo mkdir -p /opt/hpcperfstats_data
   sudo mkdir -p /opt/hpcperfstats_data/accounting
   sudo mkdir -p /opt/hpcperfstats_data/archive
   sudo mkdir -p /opt/hpcperfstats_data/daily_archive
   sudo mkdir -p /opt/hpcperfstats_data/logs/current
   sudo mkdir -p /opt/hpcperfstats_data/logs/log_archive
   ```

   The host bind mount for `hpcperfstatsdata` maps to **`/hpcperfstats/`** in the `pipeline` and `web` containers (for example `/hpcperfstats/accounting`, `/hpcperfstats/archive`, `/hpcperfstats/daily_archive`, and **`/hpcperfstats/logs/`** for cluster syslog).

   **Daily monitor archive compression:** `sync_timedb` seals each day’s `YYYY-MM-DD.tar` to **`YYYY-MM-DD.tar.zst`** with **zstd**. Defaults: **`archive_zstd_threads=0`** (**`-T0`**, niced seal/restore), **`ingest_zstd_threads=4`** (**`-T4`**, un-niced ingest/populate streams), **`archive_seal_parallel_workers=4`** (concurrent daily seals), **`nice`/`ionice`** deprioritization for web/db on unpinned hosts — see **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`** (Archive zstd priority). Other **`[PIPELINE]`** keys: `archive_zstd_level`, `archive_zstd_nice`, `archive_zstd_ionice_class`, `archive_zstd_ionice_level` in `hpcperfstats.ini.example`. When inspecting archives by hand, use for example `zstd -d -o YYYY-MM-DD.tar YYYY-MM-DD.tar.zst` (legacy `.tar.gz` uses `zstd -d --format=gzip`). Before raising `archive_zstd_level` above **9** on production data, benchmark a representative daily tar on the pipeline host: `zstd -b3 -e12 -T0 -S -- ./YYYY-MM-DD.tar`.

   **Daily archive member cache (Redis):** when **`sync_archive_members_redis_enabled=yes`** (default), `sync_timedb` requires a reachable Redis at **`[CACHE] redis_location`** (Compose default `redis://redis:6379/1`) for cross-worker sealed-archive member lookups. Startup exits non-zero if Redis is down. Ingest duplicate-check zstd runs at normal priority; janitor seal paths keep archive `nice`/`ionice`. Member maps are invalidated after tar append, dedupe, seal, and archive finalize — stale Redis/L1 must not drive delete/duplicate-check decisions. See **`sync_archive_members_redis_*`** keys in `hpcperfstats.ini.example` and **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`**.

   **Ingest-first durability vs archive failure:** with **`sync_enable_ingest_first_durability_mode=yes`** (default), DB-ingested raw may be checkpointed as processed when archive append retries are exhausted (`ingest_first_archive_abandoned_raw` in logs; entry also in **`.sync_timedb_dead_letter.json`**). Raw is **not** deleted until a later pass verifies tar+sealed archive membership. Recovery: fix archive/tar issues, clear or replay dead-letter entries, and rescan — do not delete raw manually unless you have confirmed DB and archive parity.

   **Startup snapshot wait:** on large trees with **`backlog`**, first pending rescan may wait up to **`sync_startup_snapshot_wait_seconds`** (default **300**, min **120**) for the janitor startup heavy pass to publish **`StartupArchiveScanCoordinator`** snapshot before single-flight fallback build. Grep **`sync_timedb: pending rescan begin`**, **`startup archive scan ready`**, **`janitor: discover_ready_day_close`**. Boot **`DAY_CLOSE`** discover runs on **`[sync_timedb:thread:archive-janitor]`** only — ingest is not gated on day-close completion. Inspect async manifest **`.sync_timedb_async_day_close.json`** for in-flight rows. These startup paths run only when the pipeline command includes **`backlog`** (see below).

   **Startup maintenance (`backlog` only):** janitor **`reason=startup`** heavy snapshot + boot handoff for ingest catch-up, then **`startup ingest gate cleared; ingest may begin`**. CLI **`backlog`** is ingest-only for day-close (``current`` / date-range own seal/verify/delete). Runs when **`sync_timedb.py`** is invoked with **`backlog`**. Date-window runs skip startup maintenance and begin ingest immediately.

   **`sync_timedb.py` date arguments:** with no dates, ingest uses the last five calendar days through now. A single **`YYYY-MM-DD`** limits ingest to that day only. Two dates set an explicit start/end range. Prefix **`once`** to exit after one idle rescan (for example **`once 2024-01-15`** or **`once backlog`**).

   **Startup archive scan (single-flight):** on large trees with **`backlog`**, janitor startup maintenance publishes one **`build_archive_maintenance_snapshot`** via **`StartupArchiveScanCoordinator`** — tune **`sync_startup_snapshot_wait_seconds`** (default **300**, min **120**) if logs show long waits before **`startup archive scan ready`**. Details: **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`** § canonical startup archive scan.

   **Cluster syslog (optional but typical on production clusters):** the **`pipeline`** service publishes **TCP and UDP port 514** on the Docker host. Compute or login nodes should forward syslog to **`<docker-host>:514`** (rsyslog examples: TCP `@@host:514`, UDP `@host:514`). Ingest runs in **`syslog-ng`** inside `pipeline` (not `web`). Live files are written under **`/hpcperfstats/logs/current/`** as **`$HOST.$R_YEAR$R_MONTH$R_DAY.log`** (one file per host per calendar day). After local midnight, **`seal_syslog_daily`** (supervisord) packs the previous day’s files into **`/hpcperfstats/logs/log_archive/YYYY-MM-DD-syslog.tar.gz`** and removes the sealed sources. This mirrors the “`current` vs sealed archive” story used for monitor data under `archive_dir` / `daily_archive`.

   **`[SYSLOG]` in `hpcperfstats.ini`:** set **`allow_from`** to a comma- or line-separated list of **IPv4 CIDRs** that may send remote syslog (for example `10.0.0.0/8, 192.168.50.0/24`). If **`allow_from`** is blank or **`[SYSLOG]`** is omitted, **all IPv4 sources** are accepted (backward compatible). Changing **`allow_from`** requires a **pipeline restart** so **`render_syslog_ng_generated`** can rewrite **`/var/lib/hpcperfstats-syslog/generated.conf`** (included by `syslog-ng.conf`; not under `services-conf/` so bind-mounts cannot remove it). **`listen_tcp`** / **`listen_udp`** (default `yes`) toggle listeners.

   **Operational notes:** `syslog-ng` emits periodic internal **stats** (`stats(freq(3600))` in `services-conf/syslog-ng.conf`); operators can run **`syslog-ng-ctl stats`** (as root) inside `pipeline` for counters. Monitor **disk use** on the data volume (`logs/log_archive` grows with cluster size and retention). **Troubleshooting:** if packets reach the host but nothing is logged, check **firewall rules**, that traffic targets the **published 514** on the host running `pipeline`, **`allow_from`** includes the sender’s IPv4 address, and (for filenames) that forwarders preserve a sensible hostname/FQDN.

   **Migrating from the old layout:** if you previously used a separate host path for node logs (for example `/opt/hpcperfstats_log` mounted at `/hpcperfstatslog/`), copy any `cluster.log` into **`/opt/hpcperfstats_data/logs/current/`** if you need the history, then drop the extra compose volume.

5. **Application config:**

   ```bash
   cp hpcperfstats.ini.example hpcperfstats.ini
   ```

   In `hpcperfstats.ini` under **`[DEFAULT]`** (install-required and site-wide):

   - `machine` — cluster name  
   - `host_name_ext` — FQDN of the cluster  
   - `server` — FQDN of the host running the containers
   - `restricted_queue_keywords` - queues you want to filter out and prevent jobs in them from being displayed 
   - `staff_email_domain` - the email domain of the institution/organization so authorized staff can see all jobs
   - `timezone` - your machine's local timezone
   - `total_cores` - CPU budget for app parallelism (omit to use code default **40**; see `docs/DEPLOY_CONCURRENCY_AND_NUMA.md`)
   - `secret_key` - a random string
   - PostgreSQL connection: `engine_name`, `dbname`, `username`, `password`, `host`, `port` (Compose uses `host=db` in the image-built ini)
   - Optional compose NUMA/cpuset overrides (`cpuset_pin_min_*`, `web_numa_node`, `pipeline_numa_node`, …) appear **last** in `[DEFAULT]` in the example file

   Optional tuning lives in other sections (see `hpcperfstats.ini.example`):

   - **`[PORTAL]`** — Gunicorn/Django web stack only: `max_gunicorn_workers`, `parallel_db_prefetch_max`, `api_small_executor_max_workers`, `db_conn_max_age`, `db_statement_timeout_ms`, `db_idle_in_transaction_timeout_ms`, `cors_origin_scheme`
   - **`[PIPELINE]`** — ingest, archive, and metrics: required paths `acct_path`, `archive_dir`, `daily_archive_dir`; optional `sync_*`, `metrics_*`, `archive_*`, `pipeline_overlap_mode`, and related keys
   - **`[RMQ]`**, **`[OAUTH2]`**, optional **`[CACHE]`**, **`[SYSLOG]`**, **`[XALT]`** — integration sections unchanged

   For a fresh Docker install you typically edit **`[DEFAULT]`** as above; **`[RMQ]`** and PostgreSQL defaults in the example are already wired for Compose. Do not change **`[RMQ]`** hostnames unless your RabbitMQ layout differs. For **cluster syslog**, add or edit the optional **`[SYSLOG]`** section (see `hpcperfstats.ini.example` and the compose step above).

   **`hpcperfstats.ini.example` layout:** each setting has a one-line `#` comment directly above it; optional tuning keys appear commented with defaults matching `conf_parser`. Pipeline/archive behavior (zstd seal, DB-before-append gate **`sync_archive_require_db_ingest`** under **`[PIPELINE]`**, syslog allowlist) is described in the bullets above and in **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`**.

   **Upgrading from an older ini layout:** PostgreSQL keys moved from **`[PORTAL]`** to **`[DEFAULT]`**; ingest/archive/metrics keys moved from **`[DEFAULT]`** / **`[PORTAL]`** to **`[PIPELINE]`**. Existing deployments keep working via legacy section fallbacks in `conf_parser` until you migrate keys into the new sections.

   **PostgreSQL container (`docker-compose.yaml` `db` service):** `max_connections` is **500** so overlapping Gunicorn workers, threaded API routes (`job_plots`, `home_options`, `job_detail` aux tasks), and pipeline pools rarely hit `too many clients`. Parallel helpers: **`max_worker_processes=32`**, **`max_parallel_workers=24`**, **`max_parallel_workers_per_gather=4`**, **`max_parallel_maintenance_workers=2`**. **Memory spikes** are still controlled by a **lower `work_mem`**, **smaller maintenance/autovacuum work mem**, **`temp_buffers`**, and a **slightly lower `shared_buffers`**—see inline comments there. Summary plot aggregate prefetch uses at most **two** inner threads (see `summaryplot.compute_summary_aggregate_prefetch_pool_size`) so nested thread pools do not stack against the shared API executor. If legitimate bulk jobs slow down, prefer raising `work_mem` only during batch windows or increasing the `db` container `mem_limit` rather than unconstrained per-query memory.

   For memory-constrained deployments, start with the conservative baseline values documented in `hpcperfstats.ini.example`, then scale up gradually after observing stable DB checkpoints and container RSS headroom.

   **`pipeline` memory cap (`docker-compose.app.yaml`):** on hosts with **~192 GiB RAM and no swap**, set **`mem_limit: 128g`** and **`memswap_limit: 128g`** on the **`pipeline`** service so ingest spikes cgroup-OOM inside the container before starving **`db`**/**`web`**. **`stop_grace_period`** (default **2m** in **`.example`**) allows `sync_timedb` to drain on `docker compose stop`; override with **`HPCPERFSTATS_PIPELINE_STOP_GRACE`**. After changing limits or grace, recreate the container (`docker compose up -d --force-recreate pipeline`) and verify **`memory.max`** inside the cgroup is numeric (not `max`). Pair with **`[PIPELINE]`** RSS knobs documented in **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`** § OOM.

6. **Supervisord and rsync:**

   ```bash
   cp services-conf/supervisord.conf.example services-conf/supervisord.conf
   cp services-conf/rsync_data.sh.example services-conf/rsync_data.sh
   ```

   Edit `rsync_data.sh` for your site.

7. **Web server (nginx):**

   Set **`ssl_certificate`** and **`ssl_certificate_key`** in the nginx template to the
   **`fullchain.pem`** / **`privkey.pem`** paths that match certificates mounted into the
   **`proxy`** container (compose publishes host **`/etc/letsencrypt/`** at
   **`/etc/letsencrypt/`**). Recommended: copy once so upgrades never clash with Git:

   ```bash
   cp services-conf/nginx.conf.example services-conf/nginx.conf
   ```

   Compose bind-mounts **`./services-conf/nginx.conf`** to **`/etc/nginx/http.d/default.conf`**
   on **`proxy`**, and bind-mounts the shared snippets (**`nginx-static-files.conf`**,
   **`nginx-django-proxy-common.inc`**, **`nginx-edge-security-headers.inc`**,
   **`nginx-csp-no-active.inc`**, **`nginx-csp-django-html.inc`**) as the **only**
   runtime source for those files (they are **not** baked into **`proxy.Dockerfile`**).
   The **`cp`** of **`nginx.conf.example`** → **`nginx.conf`** is **required** before
   **`docker compose up`**. Edit **`nginx.conf`** for TLS paths.
   When upgrading, merge **`ssl_trusted_certificate`**, **`include /etc/nginx/nginx-resolver.inc`**,
   and related OCSP comments from **`nginx.conf.example`** into the deployment-local
   **`nginx.conf`** (gitignored) so stapling stays complete after template changes.

   The proxy image **`cp`**s **`nginx.conf`** (or **`nginx.conf.example`**) into
   **`default.conf`** at **build** time for a non-Compose baseline, bakes
   **`hps-proxy-allowed-hosts.inc`** from **`[DEFAULT] server=`**, and ships
   **`proxy_entrypoint.sh`** plus the resolver helper. Compose still replaces
   **`default.conf`** with the host **`nginx.conf`** mount. Runtime
   **`proxy_entrypoint.sh`** regenerates the OCSP **`resolver`** include from
   container **`/etc/resolv.conf`**, waits for build-generated SPA CSP includes under
   **`/srv/static/frontend/nginx-csp-{machine,pub}.inc`**, copies them to
   **`/etc/nginx/`** and removes the **`/srv`** copies (so they are not HTTP-served),
   runs **`nginx -t`**, then starts nginx.
   Nginx is the public authority for HSTS, framing, COOP, Permissions-Policy, Referrer-Policy,
   and CSP (hash-based for SPA shells; no-active for JSON/redirects). Certificates without an
   AIA OCSP URL will not staple; that must not take the site offline.
   Hostnames come from **`[DEFAULT] server=`** in
   **`hpcperfstats.ini`** (preferred in the build context, else
   **`hpcperfstats.ini.example`**): **`parse_hpcperfstats_proxy_hosts.py`** emits
   **`/etc/nginx/hps-proxy-allowed-hosts.inc`**, which the main config **`include`**s
   for **`server_name`**. Requests whose **`Host`** header does not match receive **404**
   on port 80; on port 443, unknown names get TLS handshake rejection
   (**`ssl_reject_handshake`**). Rebuild **`proxy`** after changing **`server=`**,
   **`nginx.conf`**, or **`nginx.conf.example`**. No **`docker-compose.yaml`** edits
   are required for TLS paths or hostnames.

   Static/media routing is split into a reusable include mounted at
   **`services-conf/nginx-static-files.conf`**; nginx serves **`/static/`** and
   **`/media/`** directly, shells the SPA under **`/machine/`** and **`/pub/`**,
   and proxies only an explicit Django URL prefix list (shared **`proxy_*`**
   directives in **`services-conf/nginx-django-proxy-common.inc`**); every other
   path gets **404** from nginx. When you add a new top-level Django route,
   extend the allowlist in **`nginx-static-files.conf`** and keep it aligned with
   Django’s root **`urlpatterns`**.

   **Production:** browsers must load **`/static/*` through the `proxy` service**
   (ports 80/443); nginx reads the same **`staticfiles_data`** volume mounted at
   **`STATIC_ROOT`** on **`web`**. Hitting **`web:8000` directly** is not a
   supported way to load hashed SPA assets (Gunicorn does not implement
   **`/static/`** URL serving). For **local parity** with that layout, use full
   compose including **`proxy`**, or run **`manage.py runserver --nostatic`** and
   still obtain **`/static/`** via nginx rather than Django’s dev static handler.
   The proxy container is built from **`services-conf/proxy.Dockerfile`** and
   enables Brotli + gzip compression.

8. **Build and start:**

   ```bash
   sudo docker compose up --build -d
   ```

   View logs:

   ```bash
   sudo docker compose logs
   ```

   On first startup (or after updating the code), the `web` container runs Django
   migrations (`manage.py migrate` only — schema changes ship as reviewed,
   committed migration files; production startup never runs `makemigrations`) and
   `collectstatic` so **`STATIC_ROOT`** (the volume nginx serves as `/static/`)
   is populated before Gunicorn starts. After `collectstatic`, startup verifies
   SPA shells under **`STATIC_ROOT/frontend/{machine,pub}/index.html`** and
   compares a **sha256 fingerprint** of package vs volume `machine/index.html`.
   If shells are missing (for example a Vite-era volume after upgrading to the
   Next export), or fingerprints **differ** after a from-scratch image rebuild
   while **`staticfiles_data`** still holds an older Next tree, startup
   **auto-heals** by replacing `STATIC_ROOT/frontend` from package static.
   Image build `collectstatic` alone cannot update the named volume (it masks
   the image layer). If the package image itself lacks the shells, web
   fail-closes — rebuild target **`hpcperfstats-full`** (primary) or run
   **`./scripts/rebuild_frontend.sh`** (SPA-only hot path).

   The compose DB service includes explicit PostgreSQL checkpoint/memory tuning (`max_connections`, `shared_buffers`, `work_mem`, `maintenance_work_mem`, `autovacuum_work_mem`, `checkpoint_*`, `min_wal_size`, `max_wal_size`, and parallel-worker caps) plus `shm_size`. Keep these aligned with host RAM and service memory limits; tune upward one notch at a time only after confirming checkpoint stability and no OOM events.

   If you change the codebase, bring the containers down, make your changes, and then rebuild and start the stack again. A full **`docker compose up --build`** (or equivalent from-scratch image rebuild) plus recreating **`web`** is the primary way to land SPA fixes: startup fingerprint heal syncs the new package frontend into **`staticfiles_data`**.    Use **`./scripts/rebuild_frontend.sh`** only when you want an SPA-only refresh without rebuilding the image.
   SPA rebuilds and image builds bake the running git SHA into the staff actions menu
   (`SITE_GIT_COMMIT`). Image builds copy context `.git` into `frontend-builder` for
   `git rev-parse` (then strip `.git` from the runtime image after `COPY . .`). Optional
   `HPCPERFSTATS_GIT_COMMIT` build-arg / env still overrides when set. SPA-only
   **`./scripts/rebuild_frontend.sh`** exports the host SHA the same way.

---

## Useful commands

| Task | Command |
|------|---------|
| Build and start container stack | `sudo docker compose up --build -d` |
| Stop and remove containers | `sudo docker compose down` |
| Rebuild SPA in running stack (optional hot path; no pipeline restart) | `./scripts/rebuild_frontend.sh` |
| Rebuild web/pipeline image after Python-only changes (preserves live frontend, no npm) | `./scripts/rebuild_pipeline.sh` |
| Rebuild just the app and keep persistent services running | `docker compose -f docker-compose.app.yaml down && docker compose stop -t 120 db proxy && docker compose start db && docker compose -f docker-compose.app.yaml up --build -d && docker compose start proxy` |
| View logs  | `sudo docker compose logs` |
| PostgreSQL shell | `docker compose exec db psql -h localhost -U hpcperfstats` |
| Pipeline shell (data/processing) | `docker compose exec pipeline su hpcperfstats` |
| Get queues and message counts from rabbitmq | `docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers` |
| Admin Monitor RabbitMQ stats | Staff Admin Monitor → RabbitMQ statistics uses the **management HTTP API** on compose-internal **`http://rabbitmq:15672`** (image `rabbitmq:*-management-alpine`). Port **15672 is not published on the host**; `loopback_users.guest = false` in `services-conf/rabbitmq_management.conf` allows `web`→`rabbitmq` auth. |

---

## Publications

- [Comprehensive Resource Use Monitoring for HPC Systems with TACC Stats](http://doi.org/10.1109/HUST.2014.7)
- [Understanding application and system performance through system-wide monitoring](http://doi.org/10.1109/IPDPSW.2016.145)
- [![DOI](https://zenodo.org/badge/21212519.svg)](https://zenodo.org/badge/latestdoi/21212519)

---

## Developers and maintainers

- Amit Ruhela — aruhela@tacc.utexas.edu  
- Stephen Lien Harrell — sharrell@tacc.utexas.edu  
- Sangamithra Goutham — sgoutham@tacc.utexas.edu  
- Chris Ramos — cramos@tacc.utexas.edu  

### Developer emeritus

John Hammond · R. Todd Evans · Bill Barth · Albert Lu · Junjie Li · John McCalpin  

---

## Copyright and license

**Copyright (c) 2011 University of Texas at Austin**

This library is free software; you can redistribute it and/or modify it under the terms of the **GNU Lesser General Public License** as published by the Free Software Foundation; either version 2.1 of the License, or (at your option) any later version.

This library is distributed in the hope that it will be useful, but **without any warranty**; without even the implied warranty of merchantability or fitness for a particular purpose. See the [GNU Lesser General Public License](https://www.gnu.org/licenses/lgpl-2.1.html) for more details.

You should have received a copy of the GNU Lesser General Public License along with this library; if not, write to the Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
