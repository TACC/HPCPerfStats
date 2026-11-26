"""Turn cumulative counters (``E`` keys) into per-interval deltas and rates.

A counter key (schema flag ``E``) is a running total: the useful number is the difference
between two samples. Three things interrupt a plain subtraction:

- **Wrap.** A ``W=<bits>`` counter restarts at 0 after ``2**bits - 1``. A drop from a reading
  in the upper half of the range is taken as one wrap (the consumer's rule).
- **Reset.** Any other drop (daemon restart re-seeding a perf counter, a driver reload)
  starts a new chain: no delta for that interval.
- **Sentinels.** A ``-<n>`` token means the value was not read. Bridgeable codes (the source
  kept counting) let the next reading be differenced against the last good one; other codes
  start a new chain.
"""

from __future__ import annotations

from typing import Iterable, Iterator, NamedTuple, Optional, Tuple

from .records import Sample
from .wire import DEFAULT_COUNTER_WIDTH, Sentinel, Value


def counter_delta(prev: int, cur: int, width: int = DEFAULT_COUNTER_WIDTH) -> Optional[int]:
    """Increase of a ``width``-bit counter from ``prev`` to ``cur``; None for a reset.

    >>> counter_delta(10, 25)
    15
    >>> counter_delta(2**32 - 5, 3, 32)   # wrapped once
    8
    >>> counter_delta(1000, 10)           # dropped from the low half: reset, not a wrap
    """
    if cur >= prev:
        return cur - prev
    if prev >= 1 << (width - 1):
        return cur + (1 << width) - prev
    return None


class Point(NamedTuple):
    """One sample of one (type, dev, key).

    Attributes:
        time: Sample unix time.
        jobid: Sample job id (``-`` when idle).
        value: The reading, or a :class:`Sentinel`.
        delta: Counter increase since the previous usable reading (counters only), else None.
        rate: ``delta`` per second, else None.
        seconds: Interval the delta covers (may span bridged sentinel gaps), else None.
    """

    time: float
    jobid: str
    value: Value
    delta: Optional[int]
    rate: Optional[float]
    seconds: Optional[float]


def iter_series(
    samples: Iterable[Sample], type_name: str, dev: str, key: str, *, scaled: bool = True
) -> Iterator[Point]:
    """Time series of one key on one device, with deltas and rates for counters.

    Samples that do not carry the key (an ``@fast`` row for a slow key, a type missing from
    the sample) are skipped. ``scaled=True`` applies the ``U=`` multiplier (``U=512B``
    sectors -> bytes) to values, deltas and rates. A new ``$`` header that changes the key's
    schema starts a new chain.
    """
    last: Optional[Tuple[float, int]] = None
    last_entry = None
    for s in samples:
        row = s.row(type_name, dev)
        if row is None or key not in row.values:
            continue
        entry = next(e for e in row.entries if e.key == key)
        if last_entry is not None and entry.token != last_entry.token:
            last = None
        last_entry = entry
        mult = entry.multiplier if scaled else 1
        v = row.values[key]
        if isinstance(v, Sentinel):
            if not v.bridgeable:
                last = None
            yield Point(s.time, s.jobid, v, None, None, None)
            continue
        delta = rate = secs = None
        if entry.is_event and last is not None:
            d = counter_delta(last[1], v, entry.width)
            if d is not None:
                secs = s.time - last[0]
                delta = d * mult
                rate = delta / secs if secs > 0 else None
        last = (s.time, v)
        yield Point(s.time, s.jobid, v * mult, delta, rate, secs)
