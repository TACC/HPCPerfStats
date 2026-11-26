"""Read raw HPCPerfStats monitor data files.

Format reference: ``monitor/docs/RAW_DATA_FORMAT.md``; per-metric meanings:
``monitor/docs/METRICS_REFERENCE.md``. Standard library only (pandas optional for
:func:`to_dataframe`); Python 3.8+.

Quick start::

    import hpcperfstats_raw as hr
    for sample in hr.iter_samples("/archive/c401-001.example.org/current"):
        for row in sample.rows_of("host_cpu"):
            print(sample.time, row.dev, row.values["user"])
"""

from .counters import Point, counter_delta, iter_series
from .frame import iter_long_rows, to_dataframe
from .reader import (
    Problem,
    SampleStart,
    iter_host_archive,
    iter_records,
    iter_samples,
    open_text,
    parse_mark,
    parse_row,
    read_file,
)
from .records import Header, Mark, ProcDev, Row, Sample, parse_proc_dev
from .schema import SchemaEntry, TypeSchema, parse_schema_entry, parse_schema_line
from .wire import (
    SENTINEL_CATALOG_VERSION,
    SENTINELS,
    FormatError,
    Sentinel,
    is_sentinel,
    parse_value,
    sentinel,
)

__all__ = [
    "FormatError",
    "Header",
    "Mark",
    "Point",
    "Problem",
    "ProcDev",
    "Row",
    "SENTINELS",
    "SENTINEL_CATALOG_VERSION",
    "Sample",
    "SampleStart",
    "SchemaEntry",
    "Sentinel",
    "TypeSchema",
    "counter_delta",
    "is_sentinel",
    "iter_host_archive",
    "iter_long_rows",
    "iter_records",
    "iter_samples",
    "iter_series",
    "open_text",
    "parse_mark",
    "parse_proc_dev",
    "parse_row",
    "parse_schema_entry",
    "parse_schema_line",
    "parse_value",
    "read_file",
    "sentinel",
    "to_dataframe",
]
