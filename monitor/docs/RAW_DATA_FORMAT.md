# Reading raw monitor data files

This guide explains, line by line, the text the HPCPerfStats monitor (`hpcperfstatsd`) emits,
and how to load it with the Python reader in [`monitor/python/hpcperfstats_raw`](../python/hpcperfstats_raw).
What each individual metric means is in the companion
[METRICS_REFERENCE.md](METRICS_REFERENCE.md) (generated from the C sources).

The format is defined by the C code, not by this page. The sources that print it are listed in
[Where the format is defined](#where-the-format-is-defined); the rule
`monitor/cursor-rules/monitor-raw-data-format-docs.mdc` requires this page, the reader and the
metrics reference to change in the same commit as any of them.

## Contents

1. [Where you find these files](#where-you-find-these-files)
2. [A complete example](#a-complete-example)
3. [The five kinds of line](#the-five-kinds-of-line)
4. [Header block: `$` property lines](#header-block--property-lines)
5. [Schema lines: `!`](#schema-lines-)
6. [Sample header line](#sample-header-line)
7. [Mark lines: `%`](#mark-lines-)
8. [Data rows](#data-rows)
9. [Reading a value](#reading-a-value)
10. [Sentinels: values the monitor could not read](#sentinels-values-the-monitor-could-not-read)
11. [Fast and full samples (two-tier collection)](#fast-and-full-samples-two-tier-collection)
12. [Shared nodes: several jobs in one sample](#shared-nodes-several-jobs-in-one-sample)
13. [How a file is put together over time](#how-a-file-is-put-together-over-time)
14. [Python reader (`hpcperfstats_raw`)](#python-reader-hpcperfstats_raw)
15. [Pitfalls](#pitfalls)
16. [Where the format is defined](#where-the-format-is-defined)

## Where you find these files

All of these hold the same text format:

| Where | What it is |
|-------|------------|
| `<archive_dir>/<host>/current` and `<archive_dir>/<host>/<epoch>` on the HPCPerfStats server | What `listend.py` writes: every RabbitMQ message from the node, appended as received. A message that starts with `$` (a new header) starts a new `current`; each file is also hard-linked to a name that is the unix time of its first sample, so `current` and the newest digit file are usually the same file. |
| `/var/lib/hpcperfstats/dump/` on a compute node | Dumpfiles: samples the daemon could not send to RabbitMQ, saved to be sent later. |
| `/dev/shm/hpcperfstatsd-debug/{schema,fast,full}` on a node running a DEBUG build | The last schema message, the last `@fast` sample and the last `@full` sample (`*.burst` hold a whole shared-node tick). |
| A RabbitMQ message body | One payload: a header block and/or one or more samples. |

Text is UTF-8 (in practice ASCII), one record per line, `\n` line endings. Blank lines carry no
data; the daemon starts every sample with one.

## A complete example

Taken from the test fixture `monitor/python/tests/fixtures/archive_sample.txt` (shortened; real
files have many more types and rows):

```text
$hpcperfstats 3.1                                             <- header: daemon and version
$hostname c401-001.example.org                                <- header: node name
$uname Linux x86_64 5.14.0-570.el9.x86_64 #1 SMP PREEMPT_DYNAMIC Tue Jan 7 00:00:00 UTC 2026
$uptime 86400                                                 <- header: seconds since boot
$build x86_64-ver3.1-hw-ib-nvgpu-lustre-beegfs-x86perf-sent1-slowtier1
$sentinels 1                                                  <- header: sentinel catalog v1
!host_cpu user,E,U=cs nice,E,U=cs system,E,U=cs idle,E,U=cs iowait,E,U=cs irq,E,U=cs softirq,E,U=cs
!host_mem mem_total,U=KB mem_free,U=KB mem_used,U=KB dirty,U=KB,R=S
!intel_x86_rapl pkg_energy,E,W=32,U=mJ
                                                              <- blank line
1767225600.000000 - c401-001.example.org                      <- sample: time, job, host
host_cpu 0 @full 1000 0 500 100000 10 1 2                     <- row: type, device, tier, values
host_cpu 1 @full 1005 0 500 100000 10 1 2
host_mem 0 @full 263000000 200000000 63000000 1024
intel_x86_rapl 0 @full 4294967000

1767225630.000412 - c401-001.example.org
host_cpu 0 @fast 1300 0 650 100000 10 1 2
host_cpu 1 @fast 1305 0 650 100000 10 1 2
host_mem 0 @fast 263000000 199000000 64000000                 <- @fast: slow key `dirty` left out
intel_x86_rapl 0 @fast 200                                    <- 32-bit counter wrapped

1767225660.000107 12345 c401-001.example.org
%begin 12345                                                  <- mark: job 12345 started
host_cpu 0 @fast 1900 0 950 100000 10 1 2
...
intel_x86_rapl 0 @fast -3                                     <- sentinel: read failed
```

(The `<-` annotations are not part of the file.)

## The five kinds of line

The first character of a line decides what it is:

| First character | Line | Example |
|-----------------|------|---------|
| `$` | Header property | `$hostname c401-001.example.org` |
| `!` | Schema of one stats type | `!host_mem mem_total,U=KB mem_free,U=KB` |
| digit | Sample header | `1767225600.000000 12345 c401-001.example.org` |
| `%` | Mark (job events) | `%begin 12345` |
| letter | Data row | `host_cpu 0 @fast 1300 0 650 100000 10 1 2` |
| (empty) | Separator, no data | |

Nothing else is emitted. (`stats_buffer.c` also defines `@` and `#` prefix characters; no code
path prints a line starting with them. `@fast` / `@full` appear only inside rows.)

## Header block: `$` property lines

A header block is a run of `$` lines followed by `!` lines. It describes the daemon and lays
out every row that follows, until the next header block. The daemon writes one:

- when it starts,
- every 6 hours (`MONITOR_DAEMON_SCHEMA_ROTATE_SEC`),
- when a job ends (the same message carries the `%end` mark, see below).

A header is always followed in the same message by one complete (`@full`) sample.

The `$` lines, in the order emitted:

| Line | Meaning |
|------|---------|
| `$<program> <version>` | Daemon name (always `hpcperfstats`) and version (`3.1`). The first `$` line. |
| `$hostname <name>` | The node's name (`uname` nodename). Usually the fully qualified name; it is the same text as the host field of every sample header. |
| `$uname <sysname> <machine> <release> <version...>` | Kernel: `Linux`, CPU architecture (`x86_64`, `aarch64`), kernel release, and the kernel build string (which itself contains spaces, so everything after the third field belongs to it). |
| `$uptime <seconds>` | Seconds since the node booted, when the header was written. |
| `$build <slug>` | What this daemon binary was built with, as `-`-separated tokens: architecture, `ver<version>`, then features such as `debug` (DEBUG build), `hw` (hardware counters), `ib`/`ibdyn` (InfiniBand MAD), `opa`/`opadyn`, `nvgpu`, `amdgpu`, `intelgpu`, `lustre`, `beegfs`, the CPU counter backend (`x86perf`, `arm`, or `none`; archives from 3.0 builds say `likwid`), `sent<N>` (sentinel support) and `slowtier0`/`slowtier1` (two-tier build default). Absent in files from builds older than the slug. |
| `$sentinels <version>` | The daemon replaces unreadable values with sentinel codes from catalog version `<version>` (see [Sentinels](#sentinels-values-the-monitor-could-not-read)). Absent when the site runs `emit_value_sentinels 0`: then no value is ever negative. |

Readers should keep any other `$` line they do not recognize instead of failing on it.

## Schema lines: `!`

```text
!<type> <entry> <entry> ...
<entry> = <key>[,<option>]...
```

One line per stats type the daemon has enabled. `<type>` names the kind of rows
(`host_cpu`, `nvidia_gpu`, ...); the entries are that type's keys in the order its values
appear in a row. Options:

| Option | Meaning |
|--------|---------|
| `E` | **Counter** ("event"): a running total that only goes up (or wraps). Subtract two samples to get the amount in between. Without `E` the key is a **gauge**, an instantaneous level (memory in use, temperature, a load average). |
| `C` | Control value. The grammar allows it; no collector uses it today. |
| `U=<unit>` | Unit of the value. A leading number is a multiplier: `U=512B` means "multiply by 512 to get bytes", `U=4B` "multiply by 4 to get bytes". Units in use: `B`, `4B`, `512B`, `KB`, `kB`, `MB` (bytes and multiples; `KB` and `kB` are both 1024 bytes), `B/s`, `FLOP`, `FLOP/s`, `W` (watts), `C` (degrees Celsius), `mJ` (millijoules), `cs` (centiseconds, 1/100 s), `ms`, `us`, `ns`. No `U=` means a plain count or a dimensionless number. |
| `W=<bits>` | Counter width: the counter returns to 0 after `2^bits - 1`. Absent means 64 bits. Common values: 32 (RAPL energy, InfiniBand error counters, which saturate instead of wrapping), 48 (CPU and memory-controller PMU counters). |
| `R=S` | Slow-tier key: present only in `@full` rows when two-tier collection is on (see [Fast and full samples](#fast-and-full-samples-two-tier-collection)). |

Option letters are case-insensitive (`host_block` declares some counters with a lowercase `e`;
the daemon prints whatever the source declares). Unknown options must be ignored.

Example, read in words: `rd_sectors,E,U=512B` is "a counter of 512-byte sectors read";
`pkg_energy,E,W=32,U=mJ` is "a 32-bit counter of millijoules that wraps every 2^32 mJ";
`mem_free,U=KB` is "a gauge in KiB".

## Sample header line

```text
<time> <jobid> <host>
1767225660.000107 12345 c401-001.example.org
```

| Field | Meaning |
|-------|---------|
| `time` | Unix time (UTC seconds since 1970) when the sample was taken, with 6 decimals. All rows below it were collected at this moment. |
| `jobid` | The batch job that owned the node at that moment, or `-` when no job ran. On a node without shared-node support this comes from the job-id file the scheduler prolog writes. |
| `host` | The node name, same as `$hostname`. |

Everything after this line, up to the next sample header or header block, belongs to this
sample.

## Mark lines: `%`

Marks come right after a sample header and record events of that moment:

| Mark | When | Meaning |
|------|------|---------|
| `%begin <jobid>` | A job started on the node. | The sample is the first one of the job; its header already carries `<jobid>`. |
| `%end <jobid>` | A job ended. | Written together with a header block. The sample still carries the ending job's id; the next sample carries `-` (or the next job). |
| `%jobs jids=<id>,<id>,...` | Shared-node builds, when the node is split between jobs or one job holds only part of it. | Every job on the node at this moment. |
| `%affinity jid=<id> cpus=<list> gpus=<list> cg=<source>` | Same. | The CPUs (Linux list syntax, `0-3,8`) and GPU indices (`0,1`, or `-` for none) granted to job `<id>`. `cg=sched` says the CPU list came from the job step's scheduler affinity. |

Readers should keep marks they do not recognize.

## Data rows

```text
<type> <device> [@fast|@full] <value> <value> ...
host_cpu 0 @fast 1300 0 650 100000 10 1 2
```

| Field | Meaning |
|-------|---------|
| `type` | Stats type; its `!` line names the keys. |
| `device` | Which thing the row describes: a CPU number, a NUMA node, an interface, a disk, a GPU index, a filesystem, a process. `-` means the whole node. The device format of every type is in [METRICS_REFERENCE.md](METRICS_REFERENCE.md). |
| `@fast` / `@full` | Tier marker (absent in single-tier output). |
| values | One per key, in schema order (`@fast` rows: the non-`R=S` keys only). |

**The device can contain spaces.** `host_proc` devices are
`<process name>/<pid>/<allowed CPUs>/<allowed NUMA nodes>`, and the process name is whatever
the program called itself (`gunicorn: worker`, `Web Content`); a BeeGFS mount path can contain
a space too. Never split a row on whitespace and take field 2 as the device. Take the value
count from the schema and read the values from the right; everything between the type and the
tier marker (or the first value) is the device. Process names can also contain `/`, so split a
`host_proc` device from the right: the last three `/`-separated parts are pid, CPUs, NUMA nodes.

Other device characters to expect: `.` (`mlx5_0.1` = InfiniBand HCA `mlx5_0`, port 1), `/`
(`hfi1_0/1`, mount paths), `:` (`j:12345` for per-job memory), `-` (`scratch-clilov`).

## Reading a value

Every value is a decimal integer.

- **0 or more:** a reading. `0` really means zero ("read, and the counter was 0").
- **Negative (`-<n>`):** not a reading: a sentinel code, see the next section. Real readings are
  never negative.

To turn a reading into a quantity:

1. Multiply by the `U=` multiplier, if any (`rd_sectors` 81000 with `U=512B` is 41,472,000 bytes).
2. For a gauge, that is the answer: the level at the sample time.
3. For a counter (`E`), subtract the previous sample's reading of the same type, device and key,
   and divide by the time between the two sample headers for a rate.
   - If the new reading is smaller and the old one was in the top half of the `W=` range, the
     counter wrapped once: add `2^W`. Example: `pkg_energy` 4294967000 then 200 with `W=32` is
     `200 + 2^32 - 4294967000 = 496` mJ.
   - Any other drop is a reset (the daemon restarted, a driver reloaded, the node rebooted):
     start over from the new reading.
4. Values in a fixed unit that is not obvious are noted per key in the metrics reference (for
   example, `host_ps` load averages are multiplied by 100, `host_cpu` times are in 1/100 s).

## Sentinels: values the monitor could not read

With `$sentinels 1` in the header, a value the daemon could not read is printed as `-<code>`.
Catalog (`monitor/src/stats_sentinel.h`, append-only):

| Token | Name | Meaning | Counter chain |
|-------|------|---------|---------------|
| `-1` | `NOT_UPDATED` | No collector set the key this cycle (skipped tick, early return). | bridgeable |
| `-2` | `NOT_PRESENT` | The counter does not exist on this node (absent file, other CPU vendor's key). | breaks |
| `-3` | `READ_FAILED` | Opening, reading or parsing the source failed. | bridgeable |
| `-4` | `PERMISSION_DENIED` | The daemon was not allowed to read it. | breaks |
| `-5` | `PMU_BUSY` | The hardware counter is in use by another program. | breaks |
| `-6` | `NOT_SCHEDULED` | The perf event was not scheduled on a counter this interval. | breaks |
| `-7` | `BACKEND_ERROR` | A library call (DCGM, NVML, InfiniBand MAD, XPUM) failed. | bridgeable |
| `-8` | `INVALID_VALUE` | The source returned a blank, NaN or poisoned value. | bridgeable |
| `-9` | `BACKOFF` | The source is paused after repeated failures. | bridgeable |
| `-10` | `NOT_BUILT` | This daemon binary was built without support for the key. | breaks |
| `-11` | `COUNTER_RESET` | The counter re-seeded; the activity of that interval is lost. | breaks |

"Bridgeable" means the underlying counter kept running while the daemon could not read it, so
the next good reading can be subtracted from the last good one (the delta then covers the whole
gap). "Breaks" means start a new chain at the next good reading. A code a reader does not know
(from a newer daemon) is still not a value; treat it as breaking the chain.

Without `$sentinels` (site setting `emit_value_sentinels 0`, or archives from before sentinels)
an unreadable key carries its last value or 0, and nothing is negative.

## Fast and full samples (two-tier collection)

With two-tier collection on (the default, `enable_slow_tier 1`; `$build` ends in `slowtier1`):

- every `sample_freq` (default 30 s) the daemon writes a sample whose rows say `@fast` and
  carry only the keys without `R=S`, in schema order;
- every `sample_freq_slow` (default 600 s) it writes one whose rows say `@full` and carry every
  key;
- the sample after a header block is always `@full`.

All rows of one sample have the same tier. To read an `@fast` row, line its values up with the
schema entries that lack `R=S`. With `enable_slow_tier 0` rows have no tier token and carry
every key (this is also the shape of older archives).

## Shared nodes: several jobs in one sample

Daemons built with `--enable-shared-node-multi-jid` (for example the Horizon fleet) can split
one collection moment into several samples with **the same time**:

```text
1767225600.000000 100 g301-002.example.org
%jobs jids=100,200
%affinity jid=100 cpus=0-1 gpus=0 cg=sched
host_cpu 0 @full ...                 <- only job 100's CPUs and GPUs
host_mem_job j:100 @full ...         <- job 100's cgroup memory

1767225600.000000 200 g301-002.example.org
%jobs jids=100,200
%affinity jid=200 cpus=2 gpus=- cg=sched
host_cpu 2 @full ...

1767225600.000000 - g301-002.example.org
host_cpu 3 @full ...                 <- CPUs and GPUs no job holds
host_mem 0 @full ...                 <- node-wide rows
```

Each job's sample carries the CPU and GPU rows of what that job was granted plus its own
`host_mem_job` row; the final `-` sample carries everything unassigned and the node-wide rows.
When one job holds every CPU the daemon writes an ordinary single sample with no marks. A
counter's previous reading may therefore be in a sample with a different job id: key counter
deltas by (type, device, key), not by sample.

## How a file is put together over time

```text
[header block][full sample]            daemon start
[fast sample] x N                      every sample_freq
[full sample]                          every sample_freq_slow
[sample with %begin <job>]             job starts (no header)
...
[header block][full sample with %end]  job ends; also every 6 h and at restart
```

- In listend archives each header block starts a new file, so a file normally begins with `$`.
  Dumpfiles, DEBUG captures and concatenations may hold several header blocks, or rows with no
  header above them: carry the previous file's header forward.
- Samples are in time order within a file, except that a resent dumpfile appends older samples
  later.
- Schemas can change at a header block (daemon upgrade, new hardware detected). Always read rows
  with the schema of the header in force.
- The same sample can be delivered twice by RabbitMQ; listend drops most duplicates, so expect
  the occasional repeated (time, jobid, host) and keep the first.

## Python reader (`hpcperfstats_raw`)

A small standard-library package (Python 3.8+, pandas optional) in `monitor/python/`. Use it in
place or install it:

```sh
export PYTHONPATH=/path/to/HPCPerfStats/monitor/python
python3 -m hpcperfstats_raw summary <file>          # what is in a file
python3 -m hpcperfstats_raw schema <file>           # types, keys, units, counter/gauge
python3 -m hpcperfstats_raw series <file> host_cpu 0 user   # one key with deltas and rates (CSV)
python3 -m hpcperfstats_raw csv <file> --type host_mem       # every value, long form (CSV)
python3 -m hpcperfstats_raw check <file>            # list malformed lines, exit 1 if any
```

The examples below run as part of the test suite
(`monitor/python/tests/test_doc_examples.py`) against
`monitor/python/tests/fixtures/archive_sample.txt` (`path`) and
`shared_node_sample.txt` (`shared_path`), so they stay correct.

### Read a file sample by sample

```python
import hpcperfstats_raw as hr

for sample in hr.iter_samples(path):
    print(sample.time, sample.jobid, sample.host, sample.tier, len(sample.rows))
    for mark in sample.marks:
        print("  mark", mark.kind, mark.jids)
```

`iter_samples` accepts a path (`.gz`, `.bz2` and `.xz` are decompressed), an open file, a
list of lines or a whole payload string. Each `Sample` has `time`, `jobid`, `host`, `marks`,
`rows`, and `header` (the `Header` in force).

### The header

```python
first = next(hr.iter_samples(path))
h = first.header
print(h.program, h.version, h.hostname, h.machine, h.build_features)
print("sentinels on:", h.sentinels_version is not None)
for entry in h.schemas["host_mem"].entries:
    kind = "counter" if entry.is_event else "gauge"
    print(entry.key, kind, entry.unit, "x%d" % entry.multiplier, "slow" if entry.slow else "fast")
```

### Rows and values

```python
s = next(hr.iter_samples(path))
cpu0 = s.row("host_cpu", "0")
print(cpu0.values["user"])           # 1000 (centiseconds since boot)
print(cpu0.scaled("user"))           # same: host_cpu has no multiplier
disk = s.row("host_block", "nvme0n1")
print(disk.scaled("rd_sectors"))     # bytes: value x 512

for row in s.rows_of("host_proc"):
    proc = hr.parse_proc_dev(row.dev)          # name may contain spaces or '/'
    print(proc.name, proc.pid, row.get("vm_rss"), "kB")
```

`row.values` maps each key to an `int` or to a `Sentinel`; `row.get(key)` returns the int or
`None` (sentinel or key absent in an `@fast` row).

### Sentinels

```python
for s in hr.iter_samples(path):
    for row in s.rows:
        for key, sent in row.sentinels().items():
            print(s.time, row.type, row.dev, key, sent.slug, "bridgeable" if sent.bridgeable else "")
```

### Counter deltas and rates

```python
for p in hr.iter_series(hr.iter_samples(path), "intel_x86_rapl", "0", "pkg_energy"):
    watts = p.rate / 1000 if p.rate is not None else None   # mJ/s -> W
    print(p.time, p.value, p.delta, watts)
```

`iter_series` handles `W=` wraps, resets, bridgeable sentinel gaps and the `U=` multiplier
(`scaled=False` turns that off). `counter_delta(prev, cur, width)` is the bare arithmetic.

### A whole host archive directory

```python
samples = hr.iter_host_archive(host_dir)       # <archive>/<host>/, oldest file first
jobs = sorted({s.jobid for s in samples if s.has_job})
print(jobs)
```

### Shared-node samples

```python
by_job = {}
for s in hr.iter_samples(shared_path):
    affinity = [m for m in s.marks if m.kind == "affinity"]
    cpus = [r.dev for r in s.rows_of("host_cpu")]
    by_job[s.jobid] = (affinity[0].fields["cpus"] if affinity else None, cpus)
print(by_job)   # {'100': ('0-1', ['0', '1']), '200': ('2', ['2']), '-': (None, ['3'])}
```

### pandas

```python
try:
    import pandas  # noqa: F401
except ImportError:
    pandas = None
if pandas is not None:
    df = hr.to_dataframe(hr.iter_samples(path), types=["host_cpu"])
    wide = df.pivot_table(index="time", columns=["dev", "key"], values="value")
    print(wide.shape)
```

`to_dataframe` returns one row per value with columns `time, jobid, host, type, dev, key,
value, sentinel, unit, event`; `value` is already multiplied by the `U=` multiplier and is
empty where `sentinel` names the reason.

### Error handling

By default malformed lines are skipped; pass a list to see them, or `strict=True` to stop:

```python
problems = []
samples = list(hr.iter_samples(["host_cpu 0 1 2"], problems=problems))
print(problems)   # [Problem(line 1: row before any sample header)]
```

## Pitfalls

- Splitting a row on whitespace and taking the second token as the device breaks on process
  names with spaces (see [Data rows](#data-rows)). The ingest parser
  (`hpcperfstats/dbload/lib/sync_timedb_parsing.py` and its Codon twin) takes the device up to
  the tier marker; an untiered row keeps its last schema-count tokens as values.
- Two samples can share a timestamp (shared nodes). Index by (time, jobid).
- An `@fast` row is shorter than its schema. Line values up with the non-`R=S` keys.
- `0` is a reading, `-1` is not. Never average sentinels into data.
- `host_lnet` `msgs_alloc` / `msgs_alloc_max` are marked `E` but behave as levels; check the
  metrics reference before differencing an unfamiliar key.
- The `U=` unit text is exactly as declared: `KB` and `kB` both mean KiB; `B/s` and `FLOP/s`
  are rates already (no differencing).

## Where the format is defined

| What | Source |
|------|--------|
| Line prefix characters, sample header, payload assembly | `src/stats_buffer.c` |
| Banner lines, schema suffixes, row formatting, tier tokens | `src/stats_text_format.c` |
| Row assembly, tier choice | `src/stats_buffer_rows.c` |
| Schema option grammar | `src/schema_entry_parse.c` |
| Sentinel catalog | `src/stats_sentinel.h` |
| Slow-tier keys | `src/collect_tier.c` |
| `%begin` / `%end`, header timing | `src/monitor_daemon.c`, `src/monitor.c` |
| `%jobs` / `%affinity`, shared-node split | `src/job_shared_emit.c`, `src/job_emit_filter.c` |
| Types and keys | the `KEYS` macros of each collector (`src/*.h`, `src/*.c`) |
| Consumer (archive writer) | `hpcperfstats/listend.py` |

Python counterparts: `python/hpcperfstats_raw/wire.py` mirrors the constants and the sentinel
catalog; `python/tests/test_wire_drift.py` re-reads the C sources and fails when they differ.
