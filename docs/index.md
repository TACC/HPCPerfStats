# HPCPerfStats documentation

This page maps every HPCPerfStats doc by what you want to do.
It is for operators, developers and researchers looking for the right page.

The docs follow [Diataxis]: tutorials teach, how-to guides solve one task, reference describes, and explanation discusses why.

## Tutorials

Learn one reliable path from start to finish.

- [Install HPCPerfStats](tutorials/install-hpcperfstats.md)
- [Explore jobs in the web UI](tutorials/explore-jobs-in-the-web-ui.md)

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
- [Build Codon kernels](how-to/develop/build-codon-kernels.md)
- [Add a CPU type](how-to/develop/add-a-cpu-type.md)

### Build and run the monitor

The monitor package (`hpcperfstatsd`) starts at its [landing page](../monitor/README.md).

- [Build the monitor RPM](../monitor/docs/how-to/build-the-rpm.md)
- [Deploy the monitor to nodes](../monitor/docs/how-to/deploy-to-nodes.md)
- [Run a monitor correctness campaign](../monitor/docs/how-to/run-correctness-campaign.md)
- [Profile the monitor daemon](../monitor/docs/how-to/profile-the-daemon.md)
- [Cross-compile the monitor](../monitor/docs/how-to/cross-compile.md)
- [Refactor monitor C code file by file](../monitor/docs/C_REFACTOR_CHECKLIST.md)

## Reference

Look up code, configuration and contract facts.

- [Documentation style](reference/documentation-style.md)
- [Monitor metrics reference](../monitor/docs/METRICS_REFERENCE.md)
- [Monitor raw data format](../monitor/docs/RAW_DATA_FORMAT.md)
- [Monitor source modules](../monitor/docs/reference/source-modules.md)
- [Monitor legacy formats](../monitor/docs/reference/legacy-formats.md)
- [Pipeline configuration](reference/pipeline-configuration.md)
- [Compose services](reference/compose-services.md)
- [`sync_timedb` state machine](reference/sync-timedb-state-machine.md)
- [`sync_timedb` threads and processes](reference/sync-timedb-threads-and-processes.md)
- [`sync_timedb` stall signatures](reference/sync-timedb-stall-signatures.md)
- [`update_metrics` state machine](reference/update-metrics-state-machine.md)
- [Test layout](reference/test-layout.md)
- [Web UI metrics](reference/web-ui-metrics.md)
- [Analysis architectures](reference/analysis-architectures.md)
- [Security posture](reference/security-posture.md)
- [Release notes](reference/release-notes.md)
- [Monitor variables consumer map](reference/monitor-variables-consumer-map.md)
- [Monitor naming tables](reference/monitor-naming-tables.md)

## Explanation

Understand the design and its trade-offs.

- [Architecture](explanation/architecture.md)
- [Pipeline concurrency](explanation/pipeline-concurrency.md)
- [Codon runtime model](explanation/codon-runtime-model.md)
- [Monitor naming scheme](explanation/monitor-naming-scheme.md)
- [Monitor design](../monitor/docs/explanation/design.md)
- [Monitor x86 counters and roofline](../monitor/docs/explanation/x86-counters-and-roofline.md)

## Records

Records describe what was true at a date and are not maintained.

- [Measurements](measurements/): paper evidence for monitor overhead.
- [History](history/): dated reviews, campaigns, rollouts and incidents.
- [Plan template](plans/PLAN_TEMPLATE.md): the template for live plans.

[diataxis]: https://diataxis.fr/
