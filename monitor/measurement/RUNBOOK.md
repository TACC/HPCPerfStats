# Node monitor measurement runbook

Operator instructions for **AUTHOR_NOTES** items that run on cluster **compute nodes**
(or a dedicated node for isolation/jitter). Capture on the **same host class** you will
cite (Stampede3 SPR vs Horizon GPU are different machines and different site confs).

Copy result JSON into `monitor/measurement/results/` (gitignored), then merge into
`tests/site_measurement/results/` for `emit_paper_facts.py` (see
[`tests/site_measurement/RUNBOOK.md`](../../tests/site_measurement/RUNBOOK.md) § Paper ledger).

**Law:** if a script cannot measure it, the slot stays `NOT_MEASURED`.

Python: `python3` on the node, or `<workspace>/.venv/bin/python3` for local parser tests.
Make scripts executable (`chmod +x bin/*.sh bin/*.py`).

---

## 1. Monitor cadence (AUTHOR_NOTES §1)

On the **measured** compute node (the one you will pidstat):

```bash
cd monitor/measurement   # from HPCPerfStats git checkout
./bin/capture_monitor_cadence.sh
# or, after an overhead capture that already saved pidstat.txt:
./bin/capture_monitor_cadence.sh --pidstat /path/to/pidstat.txt
```

Reads `/etc/hpcperfstats/hpcperfstats.conf` (override with `HPCPERFSTATS_CONF`). Writes
`cadence.json` with `sample_freq`, `sample_freq_slow`, `send_freq`, `enable_slow_tier`.

Do **not** publish pidstat burst spacing as `sample_freq`. The paper already warns that
Horizon sample counts and Stampede3 busy bins are different quantities.

Repeat on Horizon if you cite Horizon jobs 2688 / 2699.

---

## 2. Daemon CPU, memory, I/O, network (AUTHOR_NOTES §2)

Same node, production `hpcperfstatsd`, ten-minute window matching the 2026-08-14 shape
(`pidstat` 10 s × 60):

```bash
./bin/capture_daemon_overhead.sh --interval 10 --count 60
# optional AMQP pcap (needs tcpdump + CAP_NET_RAW):
./bin/capture_daemon_overhead.sh --interval 10 --count 60 --tcpdump
```

Outputs `overhead.json`:

| Field | Source | Caveat |
|---|---|---|
| peak/average `%CPU` | pidstat | Percent of **one core**, not of the node |
| RSS / USS | `/proc/<pid>/status`, `smaps_rollup` | USS = private clean+dirty |
| `read_bytes` / `write_bytes` | `/proc/<pid>/io` | Block-layer storage |
| `rchar` / `wchar` | `/proc/<pid>/io` | Includes sockets |
| `total_tx_bytes` / `total_rx_bytes` | `/proc/net/dev` | **Namespace-wide upper bound**, not process-scoped |
| `ss_amqp` | `ss -tnp` port 5672 | Confirms the RabbitMQ connection |

Copy the printed directory into the paper audit folder (alongside `docs/measurements/` in
this repo if you want a second archive).

### GPU / fabric collector isolation

Requires a **dedicated** node and a conf restart. Read `bin/capture_collector_isolation.sh`
(prints the protocol). Then:

```bash
python3 bin/summarize_collector_isolation.py \
  --full FULL/overhead.json \
  --no-gpu NO_GPU/overhead.json \
  --no-fabric NO_FABRIC/overhead.json \
  --out results/collector_isolation.json
```

### OS jitter

```bash
./bin/capture_jitter.sh --label daemon_on --duration 60
# systemctl stop hpcperfstats  (dedicated node only)
./bin/capture_jitter.sh --label daemon_off --duration 60
python3 bin/summarize_jitter.py --on ON_DIR --off OFF_DIR --out results/jitter.json
```

### STREAM / IOR / OSU / one application

```bash
./bin/run_microbench_on_vs_off.sh
cp templates/apps_on_vs_off.template.json results/apps_on_vs_off.json
# fill measured numbers only
```

---

## 3. Hardware-error packet (AUTHOR_NOTES §5)

This is a **catalog** of what the monitor collectors expose today — not a new Job Detail feature.

```bash
python3 bin/catalog_hardware_error_packet.py --out results/hardware_error_packet.json
```

Confirms XIDs, ECC/row-remap, IB flaps, kernel OOM, Lustre evictions are **not**
first-class; `clocks_event_reasons` / power / temp / PCIe replay are partial.
