# Deploy to nodes

This page shows node operators how to roll a monitor RPM out to compute nodes and how to switch one collector off in an emergency.
Use it when you install or upgrade `hpcperfstatsd` on a fleet.

## Prerequisites

- An RPM built for the cluster ([build the RPM](build-the-rpm.md)).
- Root on the nodes.

## Roll out an RPM

To deploy:

1. On a shared-node fleet (Horizon is the first), upgrade the consumer (`listend`) before you install a shared-node monitor RPM, because shared-node payloads carry `%jobs` and `%affinity` marks.
2. Install the RPM on the nodes.
   The package is `hpcperfstatsd`, the systemd unit is `hpcperfstats.service`, and the binary is `/usr/sbin/hpcperfstatsd`.
3. Optional: set the shared-node conf keys `jid_require_step_id`, `jid_discover_pid_cap` and `affinity_pid_sample_limit` (shared-node builds only).

The installed `/etc/hpcperfstats/hpcperfstats.conf` is `%config(noreplace)` with mode `0600`.
An upgrade keeps a conf edited on the node and writes the packaged one as `hpcperfstats.conf.rpmnew`; an unedited conf is replaced by the packaged one.

## Disable one collector

To switch a collector off without a rebuild (for example `intel_gpu` on PVC nodes, which clears its GEM maps and VSZ), use any one of:

- `disable_types intel_gpu` in the conf.
- `--disable-types intel_gpu` on the daemon command line.
- `HPCPERFSTATS_DISABLE_TYPES=intel_gpu` in the service environment.

## Verify the result

- `systemctl status hpcperfstats --no-pager` shows the unit active.

- `rpm -q hpcperfstatsd` prints the installed release.

- The startup journal lines `Enabled types` and `Disabled types` list the collectors this run uses:

  ```bash
  journalctl -u hpcperfstats --since -1h | grep -E 'abled types'
  ```

## Troubleshooting

- `systemctl is-active hpcperfstatsd` prints `inactive`: that unit does not exist; query `hpcperfstats`.
- A changed conf did not take effect after an upgrade: the node conf was edited, so the packaged one is in `hpcperfstats.conf.rpmnew`; merge it by hand.

## Related

- [Build the RPM](build-the-rpm.md)
- [Run a correctness campaign](run-correctness-campaign.md)
- [Design](../explanation/design.md)
- [Source modules](../reference/source-modules.md)
