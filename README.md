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

- **Collected data definitions:** [attributes-definition.md](docs/attributes-definition.md)

Building and installing the `hpcperfstats-2.4-1.el9.x86_64.rpm` package (via `hpcperfstats.spec`) installs a **systemd** service `hpcperfstats`. This service runs a daemon with ~3% overhead on a single core at 1 Hz sampling; it is typically configured for **5‑minute** intervals, with samples at job start and end. The daemon **hpcperfstatsd** sends data to a **RabbitMQ** server over the administrative network. RabbitMQ must be installed and running on the server to receive data.

The **hpcperfstats** container orchestration sets up a Django/PostgreSQL ingest and archival stack plus a RabbitMQ server to receive data from the monitor on the nodes.

---

## Installation

### Monitor subpackage

1. **Install RabbitMQ development files** (build and compute nodes):

   ```bash
   sudo dnf install librabbitmq-devel
   ```

2. **Build and install:**

   ```bash
   ./configure
   make
   make install
   ```

   **Optional configure flags:**

   | Flag | Effect |
   |------|--------|
   | `--enable-mic` | Monitor Xeon Phi coprocessors |
   | `--disable-infiniband` | Disable InfiniBand monitoring |
   | `--disable-lustre` | Disable Lustre filesystem monitoring |
   | `--disable-hardware` | Disable hardware counter monitoring |

   Disabling RabbitMQ yields a legacy build that uses the shared filesystem for data; **not recommended** (testing only). If libraries or headers are not found, set `CPPFLAGS` and/or `LDFLAGS` as usual for autoconf.

3. **Configuration** — after install, edit `/etc/hpcperfstats/hpcperfstats.conf`:

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

4. **RPM build:**

   ```bash
   rpmbuild -bb hpcperfstats.spec
   ```

   The spec file uses `sed` to set server and queue in `src/hpcperfstats.conf`. Adjust for your site, e.g.:

   ```bash
   sed -i 's/localhost/stats.frontera.tacc.utexas.edu/' src/hpcperfstats.conf
   sed -i 's/default/frontera/' src/hpcperfstats.conf
   ```

5. **Service control:**

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

**Accounting file:** Generate a daily accounting file with one line per job in this format:

```
JobID|User|Account|Start|End|Submit|Partition|Timelimit|JobName|State|NNodes|ReqCPUS|NodeList
```

Example:

```
1837137|sharrell|project140208|2018-08-01T18:18:51|2018-08-02T11:44:51|2018-07-29T08:05:43|normal|1-00:00:00|jobname|COMPLETED|8|104|c420-[024,073],c421-[051-052,063-064,092-093]
```

- **SLURM users:** Use `hpcperfstats-sacct-gen` from the **hpcperfstats-tools** package; it runs `sacct` per day and POSTs to the API ingest.
- To transfer accounting from another machine, see the rsync steps in the container pipeline section below.

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
   cp docker-compose.yaml.example docker-compose.yaml
   ```

   Edit `docker-compose.yaml` and set:

   - **pipeline → volumes:** path to a `.ssh` directory with valid keys and permissions  
   - **volumes → hpcperfstatsdata → device:** path for data (your user and directory)  
   - **volumes → hpcperfstatsnodelog → device:** path for node logs (your user and directory)  

   Create the directories (e.g.):

   ```bash
   sudo mkdir -p /opt/hpcperfstats_data
   sudo mkdir -p /opt/hpcperfstats_log
   ```

5. **Application config:**

   ```bash
   cp hpcperfstats.ini.example hpcperfstats.ini
   ```

   In `hpcperfstats.ini` under `[DEFAULT]`:

   - `machine` — cluster name  
   - `host_name_ext` — FQDN of the cluster  
   - `server` — FQDN of the host running the containers  

6. **Supervisord and rsync:**

   ```bash
   cp services-conf/supervisord.conf.example services-conf/supervisord.conf
   cp services-conf/rsync_data.sh.example services-conf/rsync_data.sh
   ```

   Edit `rsync_data.sh` for your site. Use it to rsync/scp the daily accounting file (e.g. `2018-03-03.txt`) into the container at `/hpcperfstats/accounting/`.

7. **Web server (nginx):**

   **If you have SSL certificates:**

   ```bash
   cp services-conf/nginx-withssl.conf services-conf/nginx.conf
   ```

   In `nginx.conf`, set `ssl_certificate` and `ssl_certificate_key`. Note: `/etc/letsencrypt` is mounted from the host via `docker-compose.yaml`; for another path, update the compose file to match.

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

   The site will be up but may error until the accounting ingest is configured.

---

## Useful commands

| Task | Command |
|------|---------|
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
