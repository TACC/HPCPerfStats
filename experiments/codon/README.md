# Codon sync_timedb — benches and inventory (experiments)

Production Codon compile units and the in-image bake live under
**`hpcperfstats/dbload/codon/`** and Dockerfile stage **`codon-build`**.
This tree keeps A/B harnesses, inventory, and microbenches only.

## Toolchain (experiments / A/B only)

- **Codon:** pinned **0.20.6** (prod image downloads the same tarball in `codon-build`)
- Host install (optional, for inventory / codon-run spikes):

```bash
mkdir -p /data/user/$USER/codon-0.20.6
curl -fsSL -o /tmp/codon-0.20.6.tar.gz \
  https://github.com/exaloop/codon/releases/download/v0.20.6/codon-linux-x86_64.tar.gz
tar -xzf /tmp/codon-0.20.6.tar.gz -C /data/user/$USER/codon-0.20.6 --strip-components=1
export CODON_HOME=/data/user/$USER/codon-0.20.6
```

## Layout

| Path | Role |
|------|------|
| `hpcperfstats/dbload/codon/` | **Prod** sources + `build_codon_ext.sh` |
| `services-conf/pipeline_web.Dockerfile` (`codon-build`) | **Prod** bake → `/opt/hpcperfstats/codon_parse` |
| `run_codon_ab_baseline.sh` | **A/B** vs image-baked pyext |
| `run_sync_timedb_compile_inventory.sh` | Codon build attempt per `sync_timedb*.py` |
| `parse_kernel.py` / `e6_corpus.py` | CPython oracle / corpora |

## Gates

```bash
# Prod pyext: rebuild pipeline image (Dockerfile codon-build stage only)
./scripts/rebuild_pipeline.sh
# Confirm: codon_parse status=enabled in sync_timedb / pipeline logs

# Offline A/B (uses image-baked pyext when present):
./experiments/codon/run_codon_ab_baseline.sh
```

A/B pass requires `gate_1p25x=True` in `test_runs/codon_ab_*.log`.
Do **not** treat a host run of `build_codon_ext.sh` as the prod artifact.

## Tests

```bash
/data/HPCPerfStats/.venv/bin/python3 -m pytest \
  hpcperfstats/tests/test_codon_feed_collapse_class_free.py \
  hpcperfstats/tests/test_codon_feed_gpu_j_parity.py \
  hpcperfstats/tests/test_codon_runtime_bake_contract.py \
  hpcperfstats/tests/test_sync_timedb_codon_bridge.py -q
```
