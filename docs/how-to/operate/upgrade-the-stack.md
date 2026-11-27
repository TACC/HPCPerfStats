# Upgrade the stack

This page rebuilds and recreates the services of a site that already runs the HPCPerfStats Compose stack.
It is for operators; use it when you deploy a new code release, bump an image pin, rewrite the site INI, or change service limits.
For a new host, follow [Install HPCPerfStats](../../tutorials/install-hpcperfstats.md) instead.

> [!IMPORTANT]
> Read [Release notes](../../reference/release-notes.md) for every release between the running one and the new one.
> One-time cutovers (renamed services, keys and volumes) are there, not on this page.

## Prerequisites

- The checkout that holds the site `docker-compose.yaml` and `hpcperfstats.ini`, pulled to the new release.
- Run every command from that checkout.
  Per-service facts (images, limits, stop grace periods) are in [Compose services](../../reference/compose-services.md).

## Choose a rebuild script

Use a rebuild script; never `podman-compose ... up --build`.

| Task                                                                                         | Command                              |
| -------------------------------------------------------------------------------------------- | ------------------------------------ |
| Full stack: build `web`, `proxy` and `db_pg18`, then `down` and `up -d --no-build`           | `./scripts/rebuild_full_site.sh`     |
| Rebuild the `hpcperfstats` image and restart `web`, `proxy` and `pipeline`                   | `./scripts/rebuild_pipeline.sh`      |
| Rebuild only the SPA in the running stack; no pipeline restart                               | `./scripts/rebuild_frontend.sh`      |
| Recreate `web` and `pipeline` detached; no image build                                       | `./scripts/recreate_web_pipeline.sh` |
| Rebuild only the proxy image (nginx or OpenSSL pins) and recreate only `proxy` (`--no-deps`) | `./scripts/rebuild_proxy.sh`         |

`./scripts/rebuild_full_site.sh` is the primary path.
It builds the `db_pg18` image but does not start it (profile `pg18-migrate`).

`./scripts/rebuild_pipeline.sh` preserves the live SPA, takes `proxy` down (same image), then starts `web`, `proxy` and `pipeline` through the shared Podman adapter.
It leaves `db`, `valkey` and `rabbitmq` running.

`./scripts/rebuild_proxy.sh` builds while the old proxy serves, so nothing else stops.
It then checks that the container runs the new image and that `nginx -t` passes.
After a Let's Encrypt renewal or a change to `server=` or the TLS source path, run `podman-compose -p hpcperfstats restart proxy` instead.

## Deploy a code release

To deploy a release:

1. Rewrite the site INI if the release changes `hpcperfstats.ini.example` ([Rewrite the site INI](#rewrite-the-site-ini)).
2. Run `./scripts/rebuild_full_site.sh`.
   It bakes the INI into the image (immutable-image policy: never bind-mount a mutable INI over production).
3. Check the end of the output for `Stack summary`: the Compose network id, `podman-compose ps -a`, and the container and image id of each service.

The image build runs the Dockerfile stage `codon-build` (`hpcperfstats/dbload/codon/build_codon_ext.sh` inside the image) and ships `/opt/hpcperfstats/codon_parse` for `sync_timedb` (on `PYTHONPATH` in supervisord).
Never prebuild `.so` files on the host or stage them into the build context.

Image and SPA builds bake the git SHA into the staff actions menu (`SITE_GIT_COMMIT`).
The build copies the context `.git` into `frontend-builder` for `git rev-parse` and strips it from the runtime image.
A `HPCPERFSTATS_GIT_COMMIT` build arg or environment variable overrides the SHA; `./scripts/rebuild_frontend.sh` exports the host SHA the same way.

### Stop timeouts

`./scripts/rebuild_pipeline.sh` runs `supervisorctl shutdown` in `pipeline`, then `stop -t` with `HPCPERFSTATS_PIPELINE_STOP_TIMEOUT` (default 480) and `HPCPERFSTATS_WEB_STOP_TIMEOUT` (default 30).
A shorter pipeline timeout SIGKILLs programs mid-drain.

supervisord stops programs one at a time, each with `stopwaitsecs=130`.
Solo drains take about 20 s for `listend`, 30-60 s for `update_metrics` and up to 120 s for `sync_timedb`.
A clean SIGTERM exit is status 143.
A drain that passes `stopwaitsecs` prints `killing '<PROGRAM>' with SIGKILL` in the pipeline log.
`rabbitmq-watcher` and `rsync_data` use `stopsignal=KILL` on every stop.

`<PROGRAM>`: the supervisord program name.

### SPA static files

`./scripts/rebuild_full_site.sh` is the primary way to land SPA fixes: it recreates `web`, whose startup heal syncs the new frontend into `staticfiles_data`.

At startup, after `collectstatic --noinput --clear`, `web` compares a sha256 fingerprint of the package `machine/index.html` with the volume copy.
`--clear` drops unused hashed files and stale sidecars from `staticfiles_data` first.
When the shells under `STATIC_ROOT/frontend/{machine,pub}/index.html` are missing, or the fingerprints differ because the volume holds an older tree, startup replaces `STATIC_ROOT/frontend` from the package.
It then compresses sidecars and publishes `STATIC_ROOT` and `MEDIA_ROOT` onto the tmpfs volumes `staticfiles_ram` and `media_ram`.
Those are empty after a host reboot until `web` finishes that step.

An image build `collectstatic` alone cannot update the named volume, because the volume masks the image layer.
When the package image itself lacks the shells, `web` fail-closes: rebuild target `hpcperfstats-full`, or run `./scripts/rebuild_frontend.sh`, which also republishes `staticfiles_ram` so `proxy` serves the new frontend without a `web` restart.

## Change pipeline limits

To apply a new `mem_limit`, `memswap_limit` or `HPCPERFSTATS_PIPELINE_STOP_GRACE` on `pipeline`:

1. Recreate the container:

   ```bash
   podman-compose -p hpcperfstats up -d --no-build --no-deps \
     --force-recreate pipeline
   ```

2. Check that `memory.max` is a number (not `max`) and that `memory.high` is `max`:

   ```bash
   podman-compose -p hpcperfstats exec pipeline bash -lc \
     'for f in memory.max memory.high memory.current; do
       echo $f; cat /sys/fs/cgroup/$f; done'
   ```

Neither rebuild script writes `memory.high`, so the pipeline cgroup keeps the default `max`.
Do not escape `$f` through podman-compose; the loop above is single-quoted for the container shell.

## Recreate the pipeline after a supervisord change

`rabbitmq-watcher` and the other pipeline programs run from the image.
After deploying an image that adds or changes a program, recreate `pipeline` with the command in [Change pipeline limits](#change-pipeline-limits) step 1.

## Bump the Valkey image

To move `valkey` to a new image tag in `docker-compose.defaults.yaml`:

1. Pull and recreate `valkey`, check the version, and restart its clients:

   ```bash
   podman-compose -p hpcperfstats pull valkey
   podman-compose -p hpcperfstats up -d valkey
   podman-compose -p hpcperfstats exec valkey \
     valkey-cli INFO server | grep -E '^(server_name|valkey_version):'
   podman-compose -p hpcperfstats restart web pipeline
   ```

2. Expect `server_name:valkey` and `valkey_version:9.2.x` (the major.minor of the Compose pin).

Valkey has no persistence volume, so every recreate empties the cache; job plots fall back to the database artifact and `recent_host:*` refills within the `listend` TTL.
To roll back, restore the previous tag, rerun step 1, and restart `web` and `pipeline`.
When `[CACHE] valkey_location` points at an external server, upgrade that server separately, then restart the services that use the cache.

## Rewrite the site INI

`scripts/rewrite_hpcperfstats_ini.sh` rebuilds the site `hpcperfstats.ini` from the current `hpcperfstats.ini.example`.
The host has no project Python, so the script runs the checkout's `scripts/rewrite_hpcperfstats_ini.py` in a throwaway container: image `localhost/hpcperfstats:latest`, `--network none`, checkout mounted at `/work`.
It compares against this checkout's code and defaults, touches no running container, and needs no redeploy first.

To rewrite the INI:

1. Pull the release, then review the diff:

   ```bash
   ./scripts/rewrite_hpcperfstats_ini.sh --dry-run
   ```

2. Write the file:

   ```bash
   ./scripts/rewrite_hpcperfstats_ini.sh
   ```

3. Bake it into the image with a rebuild ([Deploy a code release](#deploy-a-code-release)).

The INI argument defaults to `hpcperfstats.ini`; paths are relative to the current directory, which must be inside the checkout.
`--output PATH` writes elsewhere and leaves the INI untouched; `--image IMAGE` picks another built image.

The rewrite:

- Takes every comment, section and new key from the example and fills in only the site's settings.
- Keeps a site value equal to the `conf_parser` default as a commented `# key = default` line; writes a different value as an active `key = value` line under its commented default.
- Replaces the example placeholder with the site value for required keys (no code default).
- Moves keys found in a legacy section to the example's section; a key set in more than one section keeps the value its own section wins, then `[DEFAULT]`, then `[PORTAL]` (`[PORTAL]` before `[DEFAULT]` when the INI had no `[PIPELINE]` section).
- Migrates renamed keys first through the script's `RENAMED_OPTIONS` table and prints `renamed: [SECTION] old -> new`; the value moves only when the new key is absent, and when both are set the new key wins and the old one is reported dropped.
- Drops keys the example does not document and prints `dropped (not in example): ...`.
- Refuses to write unless every key's effective value (site value, else default) is unchanged.
- Keeps the old file as `hpcperfstats.ini.bak.<UTC timestamp>` when it rewrites in place.
- Writes the new file and the `.bak` at `0600`, then applies the original owner, group and mode, so secrets are never readable mid-write.

The `owner=` it prints is the container's view: under rootless Podman, `0:0` is the host user who ran it.
The renamed and removed keys of each release are in [Release notes](../../reference/release-notes.md).

## Recreate the broker after a config change

To apply a change to the RabbitMQ limits, watermark, `ERL_FLAGS`, logging or crash-dump settings (`ERL_CRASH_DUMP_SECONDS`, `ulimits.core`), recreate `rabbitmq`:

```bash
podman-compose -p hpcperfstats up -d --force-recreate rabbitmq
```

After a change to `services-conf/rabbitmq_frame_max.conf`, recreate `rabbitmq pipeline` instead, so `listend` reconnects with the new frame size.
On hosts with less than 96 GiB RAM, lower both `mem_limit`/`memswap_limit` and the absolute watermark together ([Compose services](../../reference/compose-services.md#memory-cap-1)).

Existing queues keep their declared type when the default queue type changes.
`listend` attaches passively to an existing queue of any type, so it never fails with 406 on `x-queue-type` against a classic `stampede3`.
Never delete and recreate `stampede3` (or another `rmq_queue`) as classic to clear `listend` `INTERNAL_ERROR` 541 "timed out consuming from quorum queue".
That error is Ra consume setup and `listend` cancel churn; `listend` reconnects with backoff.
Broker recovery is in [Recover RabbitMQ](recover-rabbitmq.md).

## Upgrade RabbitMQ to a new minor

A RabbitMQ node refuses to boot on a new minor version unless every stable feature flag was enabled on the old one.
Queues and messages stay on the `rabbitmq_messages` volume.

To move to a new minor image:

1. On the running broker, enable all feature flags and count the ones still disabled:

   ```bash
   podman-compose -p hpcperfstats exec rabbitmq \
     rabbitmqctl enable_feature_flag all
   podman-compose -p hpcperfstats exec rabbitmq \
     rabbitmqctl list_feature_flags name state 2>&1 \
     | grep -c -w disabled
   ```

2. When the count is `0`, set the new tag, pull it, and recreate only `rabbitmq` and `pipeline` (so `listend` reconnects):

   ```bash
   podman-compose -p hpcperfstats pull rabbitmq
   podman-compose -p hpcperfstats up -d --no-build --no-deps \
     --force-recreate rabbitmq pipeline
   podman-compose -p hpcperfstats exec rabbitmq \
     rabbitmqctl status 2>&1 | grep -E 'RabbitMQ version|Alarms' -A1
   ```

   `status` reports the new version and `(none)` under Alarms.

## Upgrade the Timescale catalog on PostgreSQL 15

Pulling or recreating `db` updates the container libraries, but the catalog `extversion` on an existing `postgres_data` volume does not follow.
Compose pins `timescale/timescaledb:2.28.3-pg15`.
Stay on the 2.28.x line while on PostgreSQL 15 (Timescale 2.29 and later drop PG15); move to a newer Timescale only through [Migrate to PostgreSQL 18](migrate-to-postgresql-18.md).
On `db_pg18` the catalog updates itself at start ([Compose services](../../reference/compose-services.md#db-image)).

Avoid recreating `db` while a `decompress_chunk` batch is running.

To bump the catalog:

1. Compare the installed and available versions:

   ```bash
   Q="SELECT e.extversion AS installed,
     a.default_version AS available_in_image, version()
     FROM pg_extension e JOIN pg_available_extensions a
     ON a.name = e.extname WHERE e.extname = 'timescaledb'"
   podman-compose -p hpcperfstats exec -T db psql -h localhost \
     -U hpcperfstats -X -c "$Q"
   ```

   When `installed` equals `available_in_image` (`2.28.3` with the current pin), stop here.
   When `available_in_image` is older than the pin, `db` is not on the pinned image: do step 2 before `ALTER EXTENSION`.

2. Optional on a quiet site: stop the writers, then pull the pin and recreate `db` with its volume:

   ```bash
   podman-compose -p hpcperfstats stop -t 300 pipeline
   podman-compose -p hpcperfstats stop -t 120 web
   podman-compose -p hpcperfstats pull db
   podman-compose -p hpcperfstats up -d --force-recreate --no-deps db
   ```

   Wait until `pg_isready` reports healthy.
   Bring `web` and `pipeline` back only after step 3, or when you defer the catalog bump.

3. Update the extension; `ALTER EXTENSION` must be the first statement in the session (`psql -X`):

   ```bash
   podman-compose -p hpcperfstats exec -T db psql -h localhost \
     -U hpcperfstats -X -v ON_ERROR_STOP=1 \
     -c "ALTER EXTENSION timescaledb UPDATE;"
   ```

   `POSTGRES_USER` is `hpcperfstats`, a superuser in this image.
   Repeat step 1 and this step until `installed` equals `available_in_image`; a large jump (2.17 to 2.28) takes several hops.

4. Restart the services:

   ```bash
   podman-compose -p hpcperfstats up -d --no-deps web pipeline
   ```

Skip `timescaledb_toolkit` unless the database has that extension.

## Verify the result

- `./scripts/rebuild_full_site.sh` ends with `Stack summary`.

- The pipeline log shows `codon_parse status=enabled` from `sync_timedb`.

- Pipelines started from the current `supervisor_startup.sh` run at `oom_score_adj` +500, so a host OOM kills ingest before Postgres.
  Each line prints a program and `500`:

  ```bash
  podman top hpcperfstats_pipeline_1 hpid comm | sed -n 2,4p \
    | while read p c; do echo "$c $(cat /proc/$p/oom_score_adj)"; done
  ```

- The pipeline `memory.high` is `max` ([Change pipeline limits](#change-pipeline-limits) step 2).

## Troubleshooting

- `rebuild_full_site.sh` output stops at `Successfully tagged localhost/hpcperfstats:latest` after the Dockerfile `collectstatic`, with no `starting podman-compose down + up` banner: the build phase failed, usually `proxy` or `db_pg18` after `web` was tagged.
  Scroll above the trap line for `Error` or `BUILD FAILED`, or rerun `podman build -f services-conf/proxy.Dockerfile -t hpcperfstats-proxy .` from the checkout and read the failure.
  A wrong command, `--build-only`, or an outdated script on the host end the same way.

- `podman exec` into `pipeline` hangs and probes time out: a `memory.high` below `memory.current` throttles every new process in the cgroup.
  Clear it from the host:

  ```bash
  N=hpcperfstats_pipeline_1
  P=$(podman inspect $N --format '{{.State.Pid}}')
  C=$(awk -F: '$1=="0"{print $3}' /proc/$P/cgroup)
  echo max > "$(dirname /sys/fs/cgroup$C)/memory.high"
  ```

- A running pipeline shows `oom_score_adj` 0: as root on the host, set it live:

  ```bash
  for p in $(podman top hpcperfstats_pipeline_1 hpid | tail -n +2); do
    echo 500 > /proc/$p/oom_score_adj; done
  ```

- `ALTER EXTENSION timescaledb UPDATE` asks for an intermediate version: run `ALTER EXTENSION timescaledb UPDATE TO '<VERSION>';` with the version it names, then continue with plain `UPDATE` until the catalog reaches the pin.
  When the image lacks the intermediate `.so`, see the Timescale Docker upgrade notes; the HA images ship more historical libraries than the slim `timescale/timescaledb` tag.

## Related

- [Release notes](../../reference/release-notes.md)
- [Compose services](../../reference/compose-services.md)
- [Recover RabbitMQ](recover-rabbitmq.md)
- [Migrate to PostgreSQL 18](migrate-to-postgresql-18.md)
- [Pipeline configuration](../../reference/pipeline-configuration.md)
