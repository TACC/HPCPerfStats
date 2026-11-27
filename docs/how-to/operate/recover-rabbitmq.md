# Recover RabbitMQ

This page diagnoses and recovers the `rabbitmq` broker while keeping its data.
It is for operators; use it after an Erlang `binary_alloc` OOM, a storm of quorum consume errors 541, or when `rabbitmqctl` reports node not running.

> [!WARNING]
> Never delete mnesia or message-store data, including a `rabbit@<ID>` tree that looks orphaned, without an explicit written OK from the owner.
> Never convert durable monitor ingest queues (`stampede3` and the other `rmq_queue` names) to classic to clear errors.

## Prerequisites

- The broker settings in [Compose services](../../reference/compose-services.md#rabbitmq): the 96g cgroup, the 64GiB watermark (a publisher throttle, not a hard RSS ceiling), `warning` console logs, crash dumps off, the static node name, and the `rabbitmq-watcher` program.
- Alarms come from `rabbitmqctl status`; `rabbitmqctl list_alarms` does not exist on 4.4.x.

## Diagnose a node that is not running

`epmd` can still answer while Erlang is down, so check configuration, cgroup and mnesia before you suspect a cookie mismatch.

To read them in one paste:

```bash
podman-compose -p hpcperfstats exec rabbitmq sh -c '
echo === conf.d ===
for f in /etc/rabbitmq/conf.d/*.conf; do
  echo "---- $f ----"; cat "$f"
done
echo === cgroup ===
G=/sys/fs/cgroup
echo CGROUP_MAX=$(cat $G/memory.max 2>/dev/null \
  || cat $G/memory/memory.limit_in_bytes)
echo CGROUP_CUR=$(cat $G/memory.current 2>/dev/null \
  || cat $G/memory/memory.usage_in_bytes)
echo === mnesia ===
du -sh /var/lib/rabbitmq/mnesia/* 2>/dev/null | sort -h | tail -20
echo === nodename ===
echo $RABBITMQ_NODENAME; hostname
'
```

Expect `CGROUP_MAX` to be the numeric 96g (`103079215104`), the watermark `64GiB`, and console logging `warning` (not `info`, not `error` only).

## Preserve and extract stale `rabbit@*` directories

After container recreates, `/var/lib/rabbitmq/mnesia/` can hold several `rabbit@<ID>` trees beside `rabbit@rabbitmq-prod`.
The static node name stops new orphans; the old ones stay until an authorized cleanup.

To keep them safe:

1. Inventory every tree with `du -sh` (the `mnesia` section above) and treat each one as preserve.
2. Copy the trees off the volume, to a host bind or a tarball, before any offline work.
3. Extract and inspect only the copy, offline.
4. Never delete a unique production copy without explicit OK, including hex node-name directories left by past hostname changes.

## Recreate the broker and check consume

To recreate `rabbitmq` and confirm that `listend` consumes again:

1. Recreate the broker and read its status:

   ```bash
   podman-compose -p hpcperfstats up -d --force-recreate rabbitmq
   podman-compose -p hpcperfstats exec rabbitmq rabbitmqctl status
   ```

   Check Alarms and memory against the 64 GiB watermark.

2. Filter the pipeline AMQP lines; `listend` must reach consume, not only `Starting Connection`:

   ```bash
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E -e 'AMQP quorum consume-setup|Begin+ing Consume' \
       -e 'Starting Connection|timed out consuming' \
     | tail -80
   ```

3. Follow the memory trajectory; the watcher logs `mem_used` every 5 minutes and prints `ERROR` at 40 GiB and every +10 GiB above it:

   ```bash
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep '\[rabbitmq-watcher\]' | tail -40
   ```

After an OOM, no new `Crash dump is being written to: .../erl_crash.dump` line appears, because dumps are off.
To recreate `pipeline` after a watcher change, see [Upgrade the stack](upgrade-the-stack.md#recreate-the-pipeline-after-a-supervisord-change).

## Capture a one-off crash dump

To capture one dump for debugging:

1. In a local compose override, unset `ERL_CRASH_DUMP_SECONDS`, and remove `ulimits.core: 0` if you also need an OS core.
2. Recreate `rabbitmq` and capture the dump.
3. Restore the production compose and recreate `rabbitmq` again.

## Related

- [Compose services: rabbitmq](../../reference/compose-services.md#rabbitmq)
- [Upgrade the stack](upgrade-the-stack.md#recreate-the-broker-after-a-config-change)
- `hpcperfstats/cursor-rules/rabbitmq-memory-cgroup-contract.mdc`
