# Operator: RabbitMQ recovery (preserve / extract)

Use this after Erlang `binary_alloc` OOM, quorum consume **541** storms, or when `rabbitmqctl` reports **node not running**. Do **not** convert durable monitor ingest queues (`stampede3`, etc.) to **classic**. Do **not** delete mnesia / message-store data without an explicit written OK from the owner.

## Facts to keep straight

| Item | Contract |
|------|----------|
| Cgroup | Compose `mem_limit` / `memswap_limit` **96g** |
| Publisher throttle | `vm_memory_high_watermark.absolute = 80GiB` — **throttle**, not a hard RSS ceiling |
| Console logs | `log.console.level = warning` (not `info` — connection flood under thousands of publishers); `log.connection` / `log.channel` = `error` |
| Crash dumps | `ERL_CRASH_DUMP_SECONDS=0` + `ulimits.core: 0` — no `erl_crash.dump` / OS core by default |
| Nodename | Static `hostname: rabbitmq-prod` / `RABBITMQ_NODENAME=rabbit@rabbitmq-prod` |
| Alarms truth | `rabbitmqctl status` → **Alarms** (not `list_alarms` on 4.3.x) |
| Pipeline scream | supervisord `rabbitmq-watcher` — every **5 min** logs `[rabbitmq-watcher]` with `mem_used`; literal **`ERROR`** at **40 GiB** and every **+10 GiB** band (50/60/…) for log pagers |

## When `rabbitmqctl` fails (node not running)

epmd may still answer while Erlang is down. Prefer a single `sh -c` paste for conf.d / cgroup / mnesia (Sep 17 pattern) instead of assuming a cookie mismatch:

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml exec rabbitmq sh -c 'echo === conf.d ===; for f in /etc/rabbitmq/conf.d/*.conf; do echo ---- $f ----; cat "$f"; done; echo === cgroup ===; echo CGROUP_MAX=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes); echo CGROUP_CUR=$(cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes); echo === mnesia ===; du -sh /var/lib/rabbitmq/mnesia/* 2>/dev/null | sort -h | tail -20; echo === nodename ===; echo $RABBITMQ_NODENAME; hostname'
```

Confirm `CGROUP_MAX` is numeric **96g** (e.g. `103079215104`), watermark **80GiB**, logging **warning** (not `info` / not `error`-only).

## Preserve / extract stale `rabbit@*` dirs

After container recreates, `/var/lib/rabbitmq/mnesia/` may hold several `rabbit@<id>` trees plus `rabbit@rabbitmq-prod`.

1. **Inventory** with `du -sh` (above). Treat every tree as **preserve**.
2. **Copy** off-volume (host bind or tarball) before any offline work.
3. **Extract / inspect offline** only on the copy.
4. **Never delete** a unique production copy without explicit OK — including “orphan” hex nodename dirs from past hostname churn.

Static nodename already limits new orphans; leftover dirs stay until an authorized cleanup.

## Recreate after this release

Same wave as logging / crash-dump / watermark / `ERL_FLAGS` changes:

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml up -d --force-recreate rabbitmq
```

Then:

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml exec rabbitmq rabbitmqctl status
```

Check **Alarms**, Memory vs 80 GiB watermark, and that listend reaches consume (not only `Starting Connection`). Filtered pipeline AMQP lines:

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'AMQP quorum consume-setup|Begining Consume|Starting Connection|timed out consuming' | tail -80
```

Correlate memory trajectory (watcher emits every 5 minutes; ERROR bands at 40/50/60 GiB):

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep '\[rabbitmq-watcher\]' | tail -40
```

Expect no new `Crash dump is being written to: …/erl_crash.dump` lines after OOM (dumps disabled).

## Recreate pipeline after watcher ships

Watcher is a supervisord program — recreate **pipeline** (not only rabbitmq) so the new program starts:

```bash
podman-compose -p hpcperfstats -f docker-compose.yaml up -d --force-recreate pipeline
```

## One-off debug crash dump

Temporarily unset `ERL_CRASH_DUMP_SECONDS` (and remove `ulimits.core: 0` if you need an OS core) via a local compose override, recreate `rabbitmq`, capture the dump, then restore production compose and recreate again.

## Related

- `docs/upgrade.md` — existing-broker recreate notes
- `hpcperfstats/cursor-rules/rabbitmq-memory-cgroup-contract.mdc` — memory + logging + dump contract
- README Useful commands — RabbitMQ memory cap row
