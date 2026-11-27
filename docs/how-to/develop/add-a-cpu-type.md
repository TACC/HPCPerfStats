# Add a CPU type

This page shows how to teach the analysis metrics and plots a new CPU typename.
It is for analysis developers; use it when the monitor ships a typename that `hpcperfstats/analysis` does not read yet.

## Prerequisites

- The typename the monitor ships (`st_name`), which becomes `host_data.type` on ingest.
- The probe orders and naming modules described in [Analysis architectures](../../reference/analysis-architectures.md#naming).

## Add the type

To add a CPU type:

1. Add the rename entry to `docs/monitor_variable_rename_map.yaml`, then update `canonical.py` and `legacy.py` in `hpcperfstats/dbload/lib/monitor_naming/`.
2. When the type acts as PMC, add its frequency to `_PMC_FREQ_BY_TYPENAME` in `canonical.py`.
3. Extend the `resolve.py` probe orders if the type needs a new position.
4. For a new Intel IMC typename, add a roofline peak row in `hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py`.
5. Add unit tests, including `test_monitor_analysis_typename_contract.py` and `test_roofline_peaks.py`.

## Verify the result

Run the two contract tests:

```bash
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/tests/test_monitor_analysis_typename_contract.py \
  hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py
```

## Related

- [Analysis architectures](../../reference/analysis-architectures.md)
- [Run tests](run-tests.md)
