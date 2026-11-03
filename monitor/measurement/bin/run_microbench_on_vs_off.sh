#!/usr/bin/env bash
# Print the STREAM / IOR / OSU / application on-vs-off protocol.
set -euo pipefail

cat <<'EOF'
Daemon on vs off — microbenchmarks and one application
======================================================

Use a dedicated node (or an allocation you control). Do not steal a production
job's node. Keep the same binary, input, binding, and environment for both
legs. Stop or start hpcperfstatsd only between matched pairs.

Recommended pair order
----------------------
1. Warmup (discard).
2. daemon_on  (production conf, collectors enabled).
3. daemon_off (systemctl stop hpcperfstats, confirm pgrep empty).
4. daemon_on again (optional third leg to catch thermal/noise drift).

STREAM (memory)
  stream_c.exe or vendor STREAM; report Triad GB/s.
  Example: OMP_NUM_THREADS=<cores> ./stream.exe

IOR (storage)
  ior -t 1m -b 1g -F -w -r  (adjust to the site filesystem; do not hammer
  shared scratch in a way that violates policy).

OSU (fabric)
  osu_latency and osu_bw for the node interconnect (two ranks on the node
  or nearest neighbor). Record us and MB/s.

One real application
  Pick a production-like binary you already know (HPCG, a GROMACS bench,
  or a short TACC module app). Same input; report wall time and any
  internal figure of merit.

Recording
---------
Copy templates/apps_on_vs_off.template.json to results/apps_on_vs_off.json
and fill measured numbers only. Leave null for anything not run.

  python3 -c "import json,pathlib; p=pathlib.Path('results/apps_on_vs_off.json');
  print(json.loads(p.read_text()))"

Do not invent deltas. A 0.1% STREAM change inside run-to-run noise is not
evidence the daemon is free, and a noisy IOR run is not evidence it is expensive.
EOF
