# HPCPerfStats

HPCPerfStats monitors resource usage on HPC systems at multiple levels of resolution and ties it to the jobs that used it.

## Background

HPCPerfStats is the package formerly known as TACC Stats.
It has two parts:

| Component      | Build system      | Role                                                     |
| -------------- | ----------------- | -------------------------------------------------------- |
| `monitor`      | Autotools         | Online data collection and transmission on cluster nodes |
| `hpcperfstats` | Python setuptools | Data curation, analysis and the web site (off-cluster)   |

The node daemon `hpcperfstatsd`, installed from the `hpcperfstatsd-3.1-1.el9.x86_64.rpm` package as the systemd service `hpcperfstats`, samples hardware and sends the data to a RabbitMQ server over the administrative network.
On a Stampede3 Sapphire Rapids node (2026-08-14), a ten-minute `pidstat` observation of `hpcperfstatsd` showed sample-window peaks of at most 3.0% of one core and a 0.19% average over the full window ([measurement record](docs/measurements/monitor_overhead_stampede3_spr_2026-08-14.md)).

The `hpcperfstats` container stack runs RabbitMQ to receive that data, a Django/PostgreSQL ingest and archival pipeline, and the web UI.
[Architecture](docs/explanation/architecture.md) explains how the pieces fit together.

## Install

Follow [Install HPCPerfStats](docs/tutorials/install-hpcperfstats.md) for a fresh install of the central stack and the node monitor.
For an existing stack (rebuilds, Valkey, INI, volume and RabbitMQ changes, PostgreSQL 18 dual-run), use [docs/upgrade.md](docs/upgrade.md) instead.

## Quick start

On a host prepared as in the tutorial, from the checkout:

```bash
cp docker-compose.yaml.example docker-compose.yaml
cp hpcperfstats.ini.example hpcperfstats.ini
./scripts/rebuild_full_site.sh
podman-compose -p hpcperfstats logs
```

Edit both copied files before the build; the tutorial lists the keys to set.
[Common commands](docs/reference/compose-services.md#common-commands) has the everyday operator commands.

## Documentation

The full map is [docs/index.md](docs/index.md).
The most used pages:

- [Install HPCPerfStats](docs/tutorials/install-hpcperfstats.md): fresh install, step by step.
- [Compose services](docs/reference/compose-services.md): each container's image, limits, mounts and commands.
- [Pipeline configuration](docs/reference/pipeline-configuration.md): every tuning key in `hpcperfstats.ini` with its default.
- [Pipeline concurrency](docs/explanation/pipeline-concurrency.md): how threads, memory and connections are sized.
- [Using the website as a researcher](docs/using-the-website-as-a-researcher.md): reading the job UI, plots and metrics.
- [Run tests](docs/how-to/develop/run-tests.md): test commands and workflows; [Test layout](docs/reference/test-layout.md) lists the suites.
- [Monitor metrics reference](monitor/docs/METRICS_REFERENCE.md): every monitor-reported variable.

## Contributing

Follow [Run tests](docs/how-to/develop/run-tests.md) for the test workflows and [Documentation style](docs/reference/documentation-style.md) for docs.

Developers and maintainers:

- Amit Ruhela - aruhela@tacc.utexas.edu
- Stephen Lien Harrell - sharrell@tacc.utexas.edu
- Sangamithra Goutham - sgoutham@tacc.utexas.edu
- Chris Ramos - cramos@tacc.utexas.edu

Developer emeritus: John Hammond, R. Todd Evans, Bill Barth, Albert Lu, Junjie Li, John McCalpin.

Publications:

- [Resource use monitoring for HPC systems with TACC Stats (HUST 2014)](http://doi.org/10.1109/HUST.2014.7)
- [Understanding application and system performance through system-wide monitoring](http://doi.org/10.1109/IPDPSW.2016.145)
- [![DOI](https://zenodo.org/badge/21212519.svg)](https://zenodo.org/badge/latestdoi/21212519)

## License

Copyright (c) 2011 University of Texas at Austin.

This library is free software; you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation; either version 2.1 of the License, or (at your option) any later version.

This library is distributed in the hope that it will be useful, but without any warranty; without even the implied warranty of merchantability or fitness for a particular purpose.
See the [GNU Lesser General Public License](https://www.gnu.org/licenses/lgpl-2.1.html) for more details.

You should have received a copy of the GNU Lesser General Public License along with this library; if not, write to the Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
