# Install HPCPerfStats

This tutorial takes a new Rocky Linux host from nothing to a running HPCPerfStats site, then connects cluster nodes to it.
It is for HPC operators doing a fresh install; for an existing stack, follow [Upgrade the stack](../how-to/operate/upgrade-the-stack.md) instead.

You build the central container stack first, then the node monitor RPM, then wire in the scheduler and the `hpcperfstats-tools` accounting connector.
Every value you are asked to set has a reference entry in [Pipeline configuration](../reference/pipeline-configuration.md) or [Compose services](../reference/compose-services.md).

## Before you begin

You need:

- A Rocky Linux (or other EL-like) host for the central stack, with `sudo` and a large `/data` volume.
- An unprivileged deployment account on that host, which runs every container (never `sudo podman`).
- TLS certificate and key PEM files for the site hostname (`fullchain.pem` and `privkey.pem`).
- A directory of SSH keys for the pipeline, if the site rsyncs data from remote hosts.
- A Slurm login node where `sacct` works, for accounting ingest.
- Root on the cluster nodes, to install the monitor RPM and edit scheduler prolog and epilog scripts.

In the commands below, `<SERVICE_USER>` is the deployment account name and `<SITE_HOST>` is the site FQDN.

## Step 1: Install rootless Podman

To install Podman and prepare the kernel:

1. Install Podman and podman-compose:

   ```bash
   sudo dnf install -y git podman podman-compose \
     shadow-utils fuse-overlayfs slirp4netns
   ```

2. Enable memory overcommit.
   Valkey background saves use `fork()`, and the kernel can reject that fork even with free RAM; Valkey warns at startup when `vm.overcommit_memory` is off.

   ```bash
   sudo sysctl -w vm.overcommit_memory=1
   echo 'vm.overcommit_memory = 1' | \
     sudo tee /etc/sysctl.d/99-valkey-overcommit.conf
   sudo sysctl --system
   ```

   The file under `/etc/sysctl.d/` keeps the setting across reboots.
   Adding `vm.overcommit_memory = 1` to `/etc/sysctl.conf` works too.

`podman --version` prints the installed version.

## Step 2: Keep Podman storage under `/data`

Every non-DNF download, cache, tool, temporary file, image layer, writable layer and volume lives under `/data`, so the root volume never fills.

To set up the storage roots:

1. Create the per-user directories:

   ```bash
   SERVICE_USER=<SERVICE_USER>
   sudo mkdir -p /data/user/$SERVICE_USER/{cache,tmp,tools}
   sudo mkdir -p \
     /data/podman/$SERVICE_USER/{storage,images,volumes,cache,tmp}
   sudo chown -R $SERVICE_USER:$SERVICE_USER \
     /data/user/$SERVICE_USER /data/podman/$SERVICE_USER
   ```

   `<SERVICE_USER>`: the deployment account.

2. Add these login exports for the deployment account, with `<SERVICE_USER>` replaced:

   | Variable                   | Value                                           |
   | -------------------------- | ----------------------------------------------- |
   | `XDG_CACHE_HOME`           | `/data/user/<SERVICE_USER>/cache`               |
   | `TMPDIR`                   | `/data/user/<SERVICE_USER>/tmp`                 |
   | `PIP_CACHE_DIR`            | `/data/user/<SERVICE_USER>/cache/pip`           |
   | `npm_config_cache`         | `/data/user/<SERVICE_USER>/cache/npm`           |
   | `npm_config_prefix`        | `/data/user/<SERVICE_USER>/tools/npm`           |
   | `PLAYWRIGHT_BROWSERS_PATH` | `/data/user/<SERVICE_USER>/cache/ms-playwright` |

3. In the rootless `storage.conf`, set `graphroot=/data/podman/<SERVICE_USER>/storage` and `imagestore=/data/podman/<SERVICE_USER>/images`.

4. In `containers.conf`, set `volume_path=/data/podman/<SERVICE_USER>/volumes` and `image_copy_tmp_dir=/data/podman/<SERVICE_USER>/tmp`.

Small config files may stay under `~/.config`, and `/run/user/$UID` stays as required ephemeral state.

## Step 3: Allocate subordinate IDs and enable linger

Rootless subordinate IDs must cover the image uid `901860`.

To allocate them:

1. Add a non-overlapping range of at least 1,048,576 IDs to both `/etc/subuid` and `/etc/subgid`, then migrate Podman as the deployment account:

   ```bash
   sudo usermod --add-subuids 1000000-2048575 \
     --add-subgids 1000000-2048575 <SERVICE_USER>
   podman system migrate
   ```

   `<SERVICE_USER>`: the deployment account; pick a start that is unused on your site.

2. Let containers restart after a reboot:

   ```bash
   sudo loginctl enable-linger <SERVICE_USER>
   systemctl --user enable --now podman-restart.service
   ```

`podman info` shows the `/data` storage paths; check them before the first build.

## Step 4: Clone the repository and create the Compose file

To create the site Compose file:

1. Clone the repository and copy the template:

   ```bash
   git clone https://github.com/TACC/hpcperfstats.git
   cd hpcperfstats
   cp docker-compose.yaml.example docker-compose.yaml
   ```

   `docker-compose.yaml` is gitignored and includes the tracked `docker-compose.defaults.yaml` ([Compose files](../reference/compose-services.md#compose-files)).

2. In `docker-compose.yaml`, set `volumes -> ssh_keys -> device` to the host directory of pipeline SSH keys, which mounts as `/hpcperfstats/.ssh/`.

3. Set `volumes -> proxy_ssl_source -> device` to the flat directory that holds `fullchain.pem` and `privkey.pem` (the example uses `/opt/certs`).
   For Let's Encrypt, set `device: /etc/letsencrypt` and uncomment the `services.proxy.environment` block with `HPCPERFSTATS_SSL_CERTS_REL=live/your.hostname`.
   Never bind only the `live/hostname` leaf: its archive symlinks break.

4. Review the other `device:` paths; the defaults below match the example.

## Step 5: Create the host data directories

Every bind `device:` must exist before the stack starts.

To create them with the example defaults:

1. Create the data, site and database directories:

   ```bash
   sudo mkdir -p /data/hpcperfstats_data/site_data
   sudo mkdir -p /data/hpcperfstats_data/site_data/accounting
   sudo mkdir -p /data/hpcperfstats_data/site_data/archive
   sudo mkdir -p /data/hpcperfstats_data/site_data/daily_archive
   sudo mkdir -p /data/hpcperfstats_data/site_data/logs/current
   sudo mkdir -p /data/hpcperfstats_data/site_data/logs/log_archive
   sudo mkdir -p /data/hpcperfstats_data/rabbitmq
   sudo mkdir -p /data/hpcperfstats_site/staticfiles
   sudo mkdir -p /data/hpcperfstats_site/media
   sudo mkdir -p /data/hpcperfstats_db/pg15
   sudo mkdir -p /data/hpcperfstats_db/pg18
   sudo chown -R 70:70 /data/hpcperfstats_db/pg18
   ```

2. Create the `ssh_keys` and `proxy_ssl_source` device paths you chose in Step 4.

`/data/hpcperfstats_data/site_data` appears as `/hpcperfstats/` inside `pipeline` and `web`.
`/data/hpcperfstats_db/pg18` must be owned by uid and gid 70 (Alpine `postgres`); a root-owned bind fails with Permission denied when the server creates its data directory.

## Step 6: Configure `hpcperfstats.ini`

The image bakes `hpcperfstats.ini` at build time, so set it before the first build.

To configure it:

1. Copy the example:

   ```bash
   cp hpcperfstats.ini.example hpcperfstats.ini
   ```

2. Under `[DEFAULT]`, set the site keys:

   - `machine`: cluster name.
   - `host_name_ext`: FQDN suffix of the cluster nodes.
   - `server`: FQDN of the host running the containers; nginx answers only for these names.
   - `restricted_queue_keywords`: queue-name substrings whose jobs are skipped at accounting ingest, as `['secure', 'secret']` or `secure, secret`.
   - `staff_email_domain`: the email domain whose users are staff and see all jobs.
   - `timezone`: the cluster's local timezone.
   - `secret_key`: a long random string.

3. Optional: set `total_cores`, the CPU budget for application parallelism; without it the code uses `40`.

Leave the `[RMQ]`, PostgreSQL, Valkey and path defaults alone: they are wired for Compose.
[Pipeline configuration](../reference/pipeline-configuration.md) lists every other key, including the optional `[SYSLOG]` section for cluster syslog.

## Step 7: Build and start the stack

To build the images and start the site:

1. Run the full-site rebuild script from the checkout:

   ```bash
   ./scripts/rebuild_full_site.sh
   ```

   The script builds the images, then starts the default stack with `up -d --no-build`.
   The first build compiles CPython, numpy and the proxy from source, so it takes a while.

2. Watch the startup logs:

   ```bash
   podman-compose -p hpcperfstats logs
   ```

   On first start, `web` runs `manage.py migrate`, collects static files and verifies the SPA shells before Gunicorn starts ([web](../reference/compose-services.md#web)).

3. Open `https://<SITE_HOST>/` in a browser.

   `<SITE_HOST>`: a name listed in `[DEFAULT] server`.

The site shows the job list, empty until data arrives.
Use the same script for every later image refresh.

## Step 8: Build and install the node monitor

The monitor RPM builds with a static bundle: pinned `libev` and `rabbitmq-c` archives (plus `libbpf` when the eBPF profiler is enabled), linked into `hpcperfstatsd` with `--enable-all-static`.
x86 core counters come from `perf_event` directly, so no counter library is bundled.

To build and install the RPM:

1. Install the build prerequisites on the build host:

   ```bash
   sudo dnf install \
     gcc gcc-c++ make autoconf automake libtool cmake pkgconfig \
     systemd-rpm-macros gzip tar curl perl gawk pciutils \
     rdma-core-devel rpm-build
   ```

   No DCGM or NVML development package is needed on any arch: the daemon loads `libdcgm` and `libnvidia-ml` at runtime against vendored headers.

2. From `HPCPerfStats/monitor`, prepare the rpmbuild tree and source tarball:

   ```bash
   ./scripts/prepare_rpmbuild_dirs.sh
   ```

   The script removes any prior `monitor/rpmbuild/` and `monitor/embedded-static-prefix/`, creates `monitor/rpmbuild/{SPECS,SOURCES,BUILD,RPMS,SRPMS,BUILDROOT}` with `hpcperfstats.spec` in `SPECS`, runs `scripts/build_static_bundle.sh --deps-only` into `monitor/embedded-static-prefix`, runs `autoreconf -fi`, `./configure` and `make dist`, and copies `hpcperfstats-<version>.tar.gz` to `rpmbuild/SOURCES`.

3. Run the `rpmbuild` command the script prints, which looks like this:

   ```bash
   rpmbuild -ba --define "_topdir $(pwd)/rpmbuild" \
     rpmbuild/SPECS/hpcperfstats.spec
   ```

4. Install the resulting `hpcperfstatsd` RPM on each node.
   It installs the systemd service `hpcperfstats`.

Optional build variants of `scripts/build_static_bundle.sh`:

| Command                                             | Builds                                                             |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| `./scripts/build_static_bundle.sh`                  | Dependencies and the monitor binary, without rpmbuild              |
| `./scripts/build_static_bundle.sh --deps-only`      | Only the pinned dependency archives                                |
| `./scripts/build_static_bundle.sh --release`        | A release-optimized monitor (same as `HPC_BUNDLE_RELEASE_BUILD=1`) |
| `./scripts/build_static_bundle.sh --disable-lustre` | Passes extra configure arguments through                           |

## Step 9: Point the monitor at the site

The monitor reads `/etc/hpcperfstats/hpcperfstats.conf`; the installed file lists every key with a comment.

To configure and start it on each node:

1. Edit `/etc/hpcperfstats/hpcperfstats.conf`:

   | Field              | Description                                                                           |
   | ------------------ | ------------------------------------------------------------------------------------- |
   | `server`           | Hostname or IP of the RabbitMQ server (the site host)                                 |
   | `queue`            | System or cluster name being monitored                                                |
   | `port`             | RabbitMQ port (default `5672`)                                                        |
   | `sample_freq`      | Fast-tier sampling interval in seconds                                                |
   | `sample_freq_slow` | Full-sample interval in seconds (`>= sample_freq`; used when `enable_slow_tier` is 1) |
   | `send_freq`        | RabbitMQ send interval in seconds                                                     |

   The shipped values:

   ```ini
   server localhost
   queue default
   port 5672
   sample_freq 30
   sample_freq_slow 600
   send_freq 300
   ```

2. Start the service:

   ```bash
   sudo systemctl start hpcperfstats
   ```

   `sudo systemctl restart hpcperfstats` and `sudo systemctl stop hpcperfstats` restart and stop it.
   `sudo systemctl reload hpcperfstats` sends `SIGHUP` (the unit's `ExecReload`) after a config edit.

The daemon `hpcperfstatsd` sends samples to RabbitMQ over the administrative network; `podman-compose -p hpcperfstats exec rabbitmq rabbitmqctl list_queues name messages consumers` shows the queue for your `queue` name.

## Step 10: Tell the monitor about jobs

The default monitor learns the running job from `/var/run/stats_jobid` on each node.

To mark jobs, add to the scheduler prolog and epilog:

1. In the prolog, write the job ID into `/var/run/stats_jobid`.
2. In the epilog, write `-` into `/var/run/stats_jobid`.

Shared-node fleets use a monitor built with `--enable-shared-node-multi-jid` instead.
Apply migration `0036` and deploy the matching `listend` and `sync_timedb` consumer before installing that build.
It discovers job ids from process environments, does not use `/var/run/stats_jobid`, and adds `%jobs` and `%affinity` lines to each sample.
The consumer stores CPU and GPU affinity in `job_affinity` and stamps non-GPU `host_data.dev` as `j:<jid>`, so two jobs can share a host second; that `dev` key is the shared-node membership on `host_data`.
Payloads without those marks stay on empty `dev`.
An allocated node whose steps have finished stays tagged with the job, because the monitor reads the root `slurmstepd` title.
For older data, metrics count untagged samples toward a job on hosts it held alone (no other job in accounting or in `job_affinity` overlapping its window); a later accounting row for a co-tenant marks the job for recompute.

## Step 11: Load accounting data

Job records come from Slurm `sacct` through `hpcperfstats-sacct-gen` in the `hpcperfstats-tools` package.
By default it runs `sacct` for a date range and POSTs the result to the API ingest endpoint.

To set up accounting ingest on the Slurm login node:

1. Install the tools from the checkout:

   ```bash
   python3 -m pip install ./hpcperfstats-tools
   ```

   For development, `python3 -m pip install -e ./hpcperfstats-tools` installs it editable.

2. Set `HPCPERFSTATS_TOOLS_INI` to an INI file with `[API] base_url`; `hpcperfstats-tools/hpcperfstats-tools.ini.example` is the template.

3. Run the ingest as a user whose Slurm permissions cover the jobs and accounts you need, with a staff-capable API key:

   ```bash
   hpcperfstats-sacct-gen --api-key <API_KEY>
   hpcperfstats-sacct-gen 2024-01-01 2024-01-08 --api-key <API_KEY>
   ```

   `<API_KEY>`: a staff-capable key from the site.
   The first form ingests today only (default range today .. today); the second ingests an inclusive date range.

`--api-key` is optional once a key is cached: each run that passes it saves the key in `~/.hpcperfstats-api` for that `[API] base_url`, and later API-mode runs use the cached key.

Each successful POST also creates or overwrites the daily file `{acct_path}/YYYY-MM-DD.txt` (the same pipe-delimited body Slurm returned) when the body has at least one job row.
The API rejects a payload with fewer lines than the existing file for that date (HTTP 409), so a partial `sacct` export cannot replace a fuller one; header-only or empty POSTs skip the write without a 409.
API mode needs the shared data volume on `web` at `/hpcperfstats/` (as on `pipeline`), so the API can write under `acct_path` (default `/hpcperfstats/accounting`).

File mode writes the same daily files locally without calling the API:

```bash
hpcperfstats-sacct-gen -f /path/to/accounting \
  2024-01-01 2024-01-08
```

`-f DIR` is mutually exclusive with `--api-key`, `DIR` must already exist, and no API URL or key is required.
Place or sync the resulting `.txt` files where `sync_acct.py` expects them (`acct_path`); [Accounting reimport](../reference/compose-services.md#accounting-reimport) loads them.

## What you built

The site host runs the Compose stack: `proxy` serves the web UI over TLS, `web` serves Django and the API, `pipeline` archives and ingests monitor data, `db` stores it, `valkey` caches, and `rabbitmq` receives samples from the nodes.
Each node runs `hpcperfstatsd`, which samples hardware and publishes to RabbitMQ; the scheduler tells it which job is running, and `hpcperfstats-sacct-gen` loads the matching accounting records.

Next, tune pools and memory for the host size in [Pipeline configuration](../reference/pipeline-configuration.md), and enable cluster syslog or site rsync from [Compose services](../reference/compose-services.md#pipeline) if you need them.

## Related

- [Compose services](../reference/compose-services.md)
- [Pipeline configuration](../reference/pipeline-configuration.md)
- [Architecture](../explanation/architecture.md)
- [Upgrade the stack](../how-to/operate/upgrade-the-stack.md)
