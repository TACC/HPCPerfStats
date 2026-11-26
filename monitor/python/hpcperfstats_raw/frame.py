"""Optional pandas export (pandas is imported only when these functions are called)."""

from __future__ import annotations

from typing import Iterable, Iterator, Optional, Sequence, Tuple

from .records import Sample
from .wire import Sentinel

COLUMNS = ("time", "jobid", "host", "type", "dev", "key", "value", "sentinel", "unit", "event")


def iter_long_rows(
    samples: Iterable[Sample], types: Optional[Sequence[str]] = None
) -> Iterator[Tuple]:
    """One tuple per value, in :data:`COLUMNS` order.

    ``value`` is the reading times the ``U=`` multiplier (None for a sentinel), ``sentinel``
    the catalog slug (None for a reading), ``unit`` the unit after scaling, ``event`` True
    for counters.
    """
    wanted = set(types) if types else None
    for s in samples:
        for row in s.rows:
            if wanted is not None and row.type not in wanted:
                continue
            for e in row.entries:
                v = row.values[e.key]
                if isinstance(v, Sentinel):
                    yield (s.time, s.jobid, s.host, row.type, row.dev, e.key, None, v.slug,
                           e.unit, e.is_event)
                else:
                    yield (s.time, s.jobid, s.host, row.type, row.dev, e.key, v * e.multiplier,
                           None, e.unit, e.is_event)


def to_dataframe(samples: Iterable[Sample], types: Optional[Sequence[str]] = None):
    """Long-form ``pandas.DataFrame`` (one row per value) with :data:`COLUMNS`.

    Pivot it for analysis, e.g.
    ``df[df.type == "host_cpu"].pivot_table(index="time", columns=["dev", "key"], values="value")``.
    """
    import pandas as pd

    df = pd.DataFrame.from_records(list(iter_long_rows(samples, types)), columns=list(COLUMNS))
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df
