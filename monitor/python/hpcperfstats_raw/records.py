"""Parsed objects: the ``$`` header, ``%`` marks, sample rows and whole samples."""

from __future__ import annotations

from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

from .schema import SchemaEntry, TypeSchema
from .wire import NO_JOB, TIER_FAST, Value, is_sentinel


class Header:
    """The ``$`` property banner plus the ``!`` schema lines that follow it.

    A header starts every schema/rotation message: at daemon start, every 6 hours,
    and at the end of each job. All samples after it (until the next header) are
    laid out by its schemas.

    Attributes:
        program: Daemon name from ``$<program> <version>`` (``hpcperfstats``).
        version: Daemon version string (``3.1``).
        hostname: ``$hostname`` (the node's uname nodename).
        sysname, machine, release, kernel_version: The four ``$uname`` fields.
            ``kernel_version`` may contain spaces (``#1 SMP PREEMPT_DYNAMIC ...``).
        uptime: ``$uptime``: host seconds since boot when the header was written.
        build: ``$build`` capability slug (``x86_64-ver3.1-hw-ib-...``), or None
            from builds older than the slug.
        sentinels_version: ``$sentinels`` catalog version, or None when the daemon
            runs with ``emit_value_sentinels 0`` (then no ``-<n>`` tokens appear).
        properties: Every ``$`` line as ``name -> rest of line``, including any
            property this library does not interpret.
        schemas: ``type -> TypeSchema`` from the ``!`` lines, in emit order.
        line_no: 1-based line number of the first ``$`` line, when known.
    """

    def __init__(self) -> None:
        self.program: Optional[str] = None
        self.version: Optional[str] = None
        self.hostname: Optional[str] = None
        self.sysname: Optional[str] = None
        self.machine: Optional[str] = None
        self.release: Optional[str] = None
        self.kernel_version: Optional[str] = None
        self.uptime: Optional[int] = None
        self.build: Optional[str] = None
        self.sentinels_version: Optional[int] = None
        self.properties: Dict[str, str] = {}
        self.schemas: Dict[str, TypeSchema] = {}
        self.line_no: Optional[int] = None

    @property
    def build_features(self) -> List[str]:
        """``$build`` split on ``-`` (arch, version, feature tokens), or []."""
        return self.build.split("-") if self.build else []

    def __repr__(self) -> str:
        return "Header(%s %s host=%s build=%s types=%d)" % (
            self.program,
            self.version,
            self.hostname,
            self.build,
            len(self.schemas),
        )


class Mark(NamedTuple):
    """One ``%`` line, attached to the sample header it follows.

    Attributes:
        kind: First word: ``begin``, ``end``, ``jobs``, ``affinity``, or any other
            word a newer daemon emits.
        text: The full line without the ``%``.
        args: Positional words after ``kind`` (``%begin 12345`` -> ``("12345",)``).
        fields: ``key=value`` words after ``kind`` (``%jobs jids=1,2`` ->
            ``{"jids": "1,2"}``).
    """

    kind: str
    text: str
    args: Tuple[str, ...]
    fields: Dict[str, str]

    @property
    def jids(self) -> List[str]:
        """Job ids named by a ``%jobs`` (``jids=``) or ``%begin``/``%end`` mark."""
        if "jids" in self.fields:
            return [j for j in self.fields["jids"].split(",") if j]
        if "jid" in self.fields:
            return [self.fields["jid"]]
        if self.kind in ("begin", "end") and self.args:
            return [self.args[0]]
        return []


class Row(NamedTuple):
    """One ``<type> <dev> [@fast|@full] <value> ...`` line.

    Attributes:
        type: Stats type name (``host_cpu``).
        dev: Device the row describes (``0`` for CPU 0, ``eth0``, ``-`` for the host).
        tier: ``"@fast"``, ``"@full"``, or None for a legacy row (no tier token).
        values: ``key -> int`` reading, or ``key -> Sentinel`` when the daemon could
            not read it. Empty when the type had no schema (see ``raw``).
        entries: Schema entries matching ``values`` in order (fast subset for
            ``@fast`` rows).
        raw: The value tokens exactly as emitted.
        line_no: 1-based line number in the source.
    """

    type: str
    dev: str
    tier: Optional[str]
    values: Dict[str, Value]
    entries: Tuple[SchemaEntry, ...]
    raw: Tuple[str, ...]
    line_no: int

    @property
    def is_fast(self) -> bool:
        """True for an ``@fast`` row, which omits the slow (``R=S``) keys."""
        return self.tier == TIER_FAST

    def get(self, key: str) -> Optional[int]:
        """Reading for ``key``; None when absent from this row or a sentinel."""
        v = self.values.get(key)
        return None if v is None or is_sentinel(v) else v  # type: ignore[return-value]

    def scaled(self, key: str) -> Optional[int]:
        """Reading times the ``U=`` multiplier (``rd_sectors`` U=512B -> bytes)."""
        v = self.get(key)
        if v is None:
            return None
        for e in self.entries:
            if e.key == key:
                return v * e.multiplier
        return v

    def sentinels(self) -> Dict[str, Value]:
        """Only the keys that carried a sentinel, ``key -> Sentinel``."""
        return {k: v for k, v in self.values.items() if is_sentinel(v)}


class Sample:
    """Everything between one sample header line and the next.

    Attributes:
        time: Unix seconds (microsecond resolution) when the sample was taken.
        jobid: Job id the sample belongs to, ``-`` when the node ran no job.
        host: Host name (uname nodename; usually the FQDN).
        marks: ``%`` lines of this sample, in order.
        rows: Data rows, in emit order.
        header: The :class:`Header` in force, or None before any header was seen.
        line_no: 1-based line number of the sample header line.
    """

    def __init__(
        self, time: float, jobid: str, host: str, header: Optional[Header], line_no: int
    ) -> None:
        self.time = time
        self.jobid = jobid
        self.host = host
        self.header = header
        self.line_no = line_no
        self.marks: List[Mark] = []
        self.rows: List[Row] = []

    @property
    def has_job(self) -> bool:
        """True when the sample belongs to a job (``jobid`` is not ``-``)."""
        return self.jobid != NO_JOB

    @property
    def tier(self) -> Optional[str]:
        """Tier of the rows (``@fast``/``@full``/None); all rows share one tier."""
        return self.rows[0].tier if self.rows else None

    def rows_of(self, type_name: str) -> List[Row]:
        """Rows of one stats type."""
        return [r for r in self.rows if r.type == type_name]

    def row(self, type_name: str, dev: str) -> Optional[Row]:
        """The row for ``(type, dev)``, or None."""
        for r in self.rows:
            if r.type == type_name and r.dev == dev:
                return r
        return None

    def iter_values(self) -> Iterator[Tuple[str, str, str, Value]]:
        """Flatten to ``(type, dev, key, value)`` tuples."""
        for r in self.rows:
            for k, v in r.values.items():
                yield r.type, r.dev, k, v

    def __repr__(self) -> str:
        return "Sample(time=%.6f jobid=%s host=%s rows=%d marks=%d)" % (
            self.time,
            self.jobid,
            self.host,
            len(self.rows),
            len(self.marks),
        )


class ProcDev(NamedTuple):
    """The four ``/``-separated parts of a ``host_proc`` device field.

    Attributes:
        name: Process name (``comm``; may itself contain ``/``).
        pid: Process id.
        cpus_allowed: ``Cpus_allowed_list`` of the process (``0-127``).
        mems_allowed: ``Mems_allowed_list`` of the process (``0``).
    """

    name: str
    pid: int
    cpus_allowed: str
    mems_allowed: str


def parse_proc_dev(dev: str) -> ProcDev:
    """Split a ``host_proc`` device such as ``python3/4121/0-127/0``.

    Splits from the right so a process name containing ``/`` stays whole.
    """
    name, pid, cpus, mems = dev.rsplit("/", 3)
    return ProcDev(name, int(pid), cpus, mems)
