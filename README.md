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

**Maintaining `MONITOR_VARIABLES.md`:** the catalog is generated and augmented by maintainer scripts in the same folder: [`regenerate_monitor_variables_catalog.py`](docs/regenerate_monitor_variables_catalog.py), [`augment_monitor_variables_diagnostics.py`](docs/augment_monitor_variables_diagnostics.py).

**REST API note:** `GET /api/jobs/{jid}/{type_name}/` (type detail) returns a Bokeh **`tplot_item`** (`json_item` payload) plus `stats_data` / `schema`. Legacy **`tscript`** / **`tdiv`** fields were removed; clients should embed only `tplot_item` via Bokeh `embed_item`.

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

**Accounting ingest (SLURM `sacct`):** Instead of generating and transferring daily accounting files, use `hpcperfstats-sacct-gen` from the **hpcperfstats-tools** package. This command runs `sacct` for a date range and POSTs the results directly to the HPCPerfStats API ingest endpoint.

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
   ```

**Run-location and permissions requirements:**

- Run `hpcperfstats-sacct-gen` on a host where Slurm’s `sacct` binary exists and works (typically a Slurm login node).
- Run it as a user that has the correct Slurm permissions to query the relevant jobs/accounts via `sacct`.
- The API key you pass with `--api-key` must be staff-capable for the ingest endpoint.

---

### hpcperfstats subpackage (container stack)

This is a container orchestration with Django/PostgreSQL, ingest/archival tools, and RabbitMQ. The steps below assume a **Rocky Linux** host.

1. **Install Docker/Podman:**

   ```bash
   sudo dnf install docker git podman-compose
   ```

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

   **Cluster syslog (optional but typical on production clusters):** the **`pipeline`** service publishes **TCP and UDP port 514** on the Docker host. Compute or login nodes should forward syslog to **`<docker-host>:514`** (rsyslog examples: TCP `@@host:514`, UDP `@host:514`). Ingest runs in **`syslog-ng`** inside `pipeline` (not `web`). Live files are written under **`/hpcperfstats/logs/current/`** as **`$HOST.$R_YEAR$R_MONTH$R_DAY.log`** (one file per host per calendar day). After local midnight, **`seal_syslog_daily`** (supervisord) packs the previous day’s files into **`/hpcperfstats/logs/log_archive/YYYY-MM-DD-syslog.tar.gz`** and removes the sealed sources. This mirrors the “`current` vs sealed archive” story used for monitor data under `archive_dir` / `daily_archive`.

   **`[SYSLOG]` in `hpcperfstats.ini`:** set **`allow_from`** to a comma- or line-separated list of **IPv4 CIDRs** that may send remote syslog (for example `10.0.0.0/8, 192.168.50.0/24`). If **`allow_from`** is blank or **`[SYSLOG]`** is omitted, **all IPv4 sources** are accepted (backward compatible). Changing **`allow_from`** requires a **pipeline restart** so **`render_syslog_ng_generated`** can rewrite **`/var/lib/hpcperfstats-syslog/generated.conf`** (included by `syslog-ng.conf`; not under `services-conf/` so bind-mounts cannot remove it). **`listen_tcp`** / **`listen_udp`** (default `yes`) toggle listeners.

   **Operational notes:** `syslog-ng` emits periodic internal **stats** (`stats(freq(3600))` in `services-conf/syslog-ng.conf`); operators can run **`syslog-ng-ctl stats`** (as root) inside `pipeline` for counters. Monitor **disk use** on the data volume (`logs/log_archive` grows with cluster size and retention). **Troubleshooting:** if packets reach the host but nothing is logged, check **firewall rules**, that traffic targets the **published 514** on the host running `pipeline`, **`allow_from`** includes the sender’s IPv4 address, and (for filenames) that forwarders preserve a sensible hostname/FQDN.

   **Migrating from the old layout:** if you previously used a separate host path for node logs (for example `/opt/hpcperfstats_log` mounted at `/hpcperfstatslog/`), copy any `cluster.log` into **`/opt/hpcperfstats_data/logs/current/`** if you need the history, then drop the extra compose volume.

5. **Application config:**

   ```bash
   cp hpcperfstats.ini.example hpcperfstats.ini
   ```

   In `hpcperfstats.ini` under `[DEFAULT]`:

   - `machine` — cluster name  
   - `host_name_ext` — FQDN of the cluster  
   - `server` — FQDN of the host running the containers
   - `restricted_queue_keywords` - queues you want to filter out and prevent jobs in them from being displayed 
   - `staff_email_domain` - the email domain of the institution/organization so authorized staff can see all jobs
   - `timezone` - your machine's local timezone
   - `total_cores` - CPU budget for app parallelism (omit to use code default **40**; see `docs/DEPLOY_CONCURRENCY_AND_NUMA.md`)
   - `secret_key` - a random string

   You will only need to edit the `[DEFAULT]` section as detailed above. The `[RMQ]` and `[PORTAL]` sections have been configured to work for the docker installation, and we do not recommend changing any of the variables in these sections.
   If you need to edit some of those variables, please note that a lot of them are tied to the docker yaml file. For **cluster syslog**, add or edit the optional **`[SYSLOG]`** section (see `hpcperfstats.ini.example` and the compose step above).

6. **Supervisord and rsync:**

   ```bash
   cp services-conf/supervisord.conf.example services-conf/supervisord.conf
   cp services-conf/rsync_data.sh.example services-conf/rsync_data.sh
   ```

   Edit `rsync_data.sh` for your site.

7. **Web server (nginx):**

   **If you have SSL certificates:**

   ```bash
   cp services-conf/nginx-withssl.conf services-conf/nginx.conf
   ```

   In `nginx.conf`, set `ssl_certificate` and `ssl_certificate_key`. Note: `/etc/letsencrypt` is mounted from the host via `docker-compose.yaml`; for another path, update the compose file to match.
   Static/media routing is split into a reusable include mounted at
   `services-conf/nginx-static-files.conf`; both SSL and non-SSL nginx configs
   include this file so nginx serves `/static/` and `/media/` directly, shells the
   SPA under `/machine/` and `/pub/`, and proxies only an explicit Django URL
   prefix list (shared `proxy_*` directives in
   `services-conf/nginx-django-proxy-common.inc`); every other path gets **404**
   from nginx. When you add a new top-level Django route, extend the allowlist in
   `nginx-static-files.conf`.
   **Production:** browsers must load **`/static/*` through the `proxy` service**
   (ports 80/443); nginx reads the same `staticfiles_data` volume mounted at
   `STATIC_ROOT` on `web`. Hitting **`web:8000` directly** is not a supported way
   to load hashed SPA assets (Gunicorn does not implement `/static/` URL
   serving). For **local parity** with that layout, use full compose including
   `proxy`, or run `manage.py runserver --nostatic` and still obtain `/static/`
   via nginx rather than Django’s dev static handler.
   The proxy container is built from `services-conf/proxy.Dockerfile` and enables
   Brotli + gzip compression.

   **If you do not have SSL (testing only):**

   ```bash
   cp services-conf/nginx-nossl.conf services-conf/nginx.conf
   ```

8. **Build and start:**

   ```bash
   sudo docker compose up --build -d
   ```

   View logs:

   ```bash
   sudo docker compose logs
   ```

   On first startup (or after updating the code), the `web` container runs Django
   migrations (`manage.py makemigrations` and `manage.py migrate`) and
   `collectstatic` so **`STATIC_ROOT`** (the volume nginx serves as `/static/`)
   is populated before Gunicorn starts.

   If you change the codebase, bring the containers down, make your changes, and then rebuild and start the stack again.

---

## Useful commands

| Task | Command |
|------|---------|
| Build and start container stack | `sudo docker compose up --build -d` |
| Stop and remove containers | `sudo docker compose down` |
| Rebuild just the app and keep persistent services running | `docker compose -f docker-compose.app.yaml down &&  docker stop -t 120 hpcperfstats_db_1 && docker stop hpcperfstats_proxy_1 && docker start hpcperfstats_db_1 && docker compose -f docker-compose.app.yaml up --build -d && docker start hpcperfstats_proxy_1`
| View logs  | `sudo docker compose logs` |
| PostgreSQL shell | `docker exec -it hpcperfstats_db_1 psql -h localhost -U hpcperfstats` |
| Pipeline shell (data/processing) | `docker exec -it hpcperfstats_pipeline_1 su hpcperfstats` |
| Get queues and message counts from rabbitmq | `docker exec -it hpcperfstats_rabbitmq_1 rabbitmqctl list_queues` |

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
