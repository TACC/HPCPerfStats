# HPCPerfStats documentation

This page maps every HPCPerfStats doc by what you want to do.
It is for operators, developers and researchers looking for the right page.

The docs follow [Diataxis]: tutorials teach, how-to guides solve one task, reference describes, and explanation discusses why.
Pages listed without a link are planned and not written yet.

## Tutorials

Learn one reliable path from start to finish.

- [Install HPCPerfStats](tutorials/install-hpcperfstats.md)
- Explore jobs in the web UI

## How-to guides

Solve one task on a running site or in the checkout.

### Operate a site

- [Upgrade the stack](how-to/operate/upgrade-the-stack.md)
- [Recover RabbitMQ](how-to/operate/recover-rabbitmq.md)
- [Verify `sync_timedb`](how-to/operate/verify-sync-timedb.md)
- [Migrate to PostgreSQL 18](how-to/operate/migrate-to-postgresql-18.md)

### Develop in the checkout

- [Run tests](how-to/develop/run-tests.md)
- [Run `sync_timedb` benchmarks](how-to/develop/run-sync-timedb-benchmarks.md)
- [Diagnose `update_metrics`](how-to/develop/diagnose-update-metrics.md)
- Build Codon kernels
- Add a CPU type

## Reference

Look up code, configuration and contract facts.

- [Documentation style](reference/documentation-style.md)
- [Monitor metrics reference](../monitor/docs/METRICS_REFERENCE.md)
- [Monitor raw data format](../monitor/docs/RAW_DATA_FORMAT.md)
- [Pipeline configuration](reference/pipeline-configuration.md)
- [Compose services](reference/compose-services.md)
- `sync_timedb` state machine
- `sync_timedb` threads and processes
- [`sync_timedb` stall signatures](reference/sync-timedb-stall-signatures.md)
- `update_metrics` state machine
- [Test layout](reference/test-layout.md)
- Web UI metrics
- Analysis architectures
- Security posture
- [Release notes](reference/release-notes.md)
- Monitor variables consumer map

## Explanation

Understand the design and its trade-offs.

- [Architecture](explanation/architecture.md)
- [Pipeline concurrency](explanation/pipeline-concurrency.md)
- Codon runtime model
- Monitor naming scheme

## Records

Records describe what was true at a date and are not maintained.

- [Measurements](measurements/): paper evidence for monitor overhead.
- History: dated reviews, campaigns and rollouts.
- [Plan template](plans/PLAN_TEMPLATE.md): the template for live plans.

[diataxis]: https://diataxis.fr/
