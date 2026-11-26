"""Read monitor text: archive files, dumpfiles, DEBUG ``/dev/shm`` captures, raw payloads.

Two layers:

- :func:`iter_records` yields one parsed object per meaningful line (``Header``, ``Sample``
  start, ``Mark``, ``Row``) for callers that want the raw line stream;
- :func:`iter_samples` / :func:`read_file` group those into :class:`Sample` objects, each
  carrying its marks, rows and the :class:`Header` in force.

Line grammar (one physical line each; blank lines separate payloads and carry no data):

====================  ==============================================================
``$<name> <rest>``    property banner line; starts a new header (schema block)
``!<type> <entry>..`` schema of one type; belongs to the header above it
``<time> <jid> <host>``  sample header (first character is a digit)
``%<kind> <args>``    mark attached to the current sample (``%begin``, ``%jobs`` ...)
``<type> <dev> ...``  data row (first character is a letter)
====================  ==============================================================
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
from typing import IO, Iterable, Iterator, List, Optional, Tuple, Union

from .records import Header, Mark, Row, Sample
from .schema import TypeSchema, parse_schema_line
from .wire import MARK_CHAR, PROPERTY_CHAR, SCHEMA_CHAR, TIER_FAST, TIER_TOKENS, FormatError, parse_value

PathLike = Union[str, "os.PathLike[str]"]
Source = Union[PathLike, IO[str], Iterable[str]]

_OPENERS = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}


class SampleStart:
    """Record yielded by :func:`iter_records` for a ``<time> <jid> <host>`` line."""

    __slots__ = ("time", "jobid", "host", "line_no")

    def __init__(self, time: float, jobid: str, host: str, line_no: int) -> None:
        self.time, self.jobid, self.host, self.line_no = time, jobid, host, line_no

    def __repr__(self) -> str:
        return "SampleStart(%.6f %s %s)" % (self.time, self.jobid, self.host)


class Problem:
    """A line the reader skipped (non-strict mode). ``message`` says why."""

    __slots__ = ("line_no", "line", "message")

    def __init__(self, line_no: int, line: str, message: str) -> None:
        self.line_no, self.line, self.message = line_no, line, message

    def __repr__(self) -> str:
        return "Problem(line %d: %s)" % (self.line_no, self.message)


Record = Union[Header, SampleStart, Mark, Row, Problem]


def open_text(path: PathLike) -> IO[str]:
    """Open a monitor file as UTF-8 text; ``.gz`` / ``.bz2`` / ``.xz`` are decompressed."""
    opener = _OPENERS.get(os.path.splitext(str(path))[1])
    if opener is not None:
        return io.TextIOWrapper(opener(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _lines(source: Source) -> Tuple[Iterable[str], Optional[IO[str]]]:
    if isinstance(source, (str, bytes)) or hasattr(source, "__fspath__"):
        if isinstance(source, str) and "\n" in source:
            return source.splitlines(), None
        fh = open_text(source)  # type: ignore[arg-type]
        return fh, fh
    return source, None  # type: ignore[return-value]


def parse_mark(line: str) -> Mark:
    """Parse ``%<kind> word word key=value ...``."""
    text = line.strip()[1:]
    words = text.split()
    kind = words[0] if words else ""
    args, fields = [], {}
    for w in words[1:]:
        k, eq, v = w.partition("=")
        if eq:
            fields[k] = v
        else:
            args.append(w)
    return Mark(kind, text, tuple(args), fields)


def _apply_property(header: Header, line: str) -> None:
    name, _, rest = line.strip()[1:].partition(" ")
    header.properties[name] = rest
    if name == "hostname":
        header.hostname = rest
    elif name == "uname":
        parts = rest.split(" ", 3)
        parts += [""] * (4 - len(parts))
        header.sysname, header.machine, header.release, header.kernel_version = parts
    elif name == "uptime":
        header.uptime = int(rest) if rest.isdigit() else None
    elif name == "build":
        header.build = rest or None
    elif name == "sentinels":
        header.sentinels_version = int(rest) if rest.isdigit() else None
    elif header.program is None:
        # The first banner line is `$<program> <version>`.
        header.program, header.version = name, rest


def parse_row(
    line: str, schemas: Optional[dict], line_no: int = 0
) -> Row:
    """Parse one data row against ``schemas`` (``type -> TypeSchema``).

    The value count comes from the schema (all keys, or the fast subset for ``@fast``), and
    values are taken from the right, so a device name containing spaces (a ``host_proc``
    process name such as ``Web Content``, a BeeGFS mount path) stays whole.

    Without a schema for the type the row keeps its tokens in ``raw`` and ``values`` is empty;
    the device is then assumed to be the single token after the type.

    Raises:
        FormatError: Fewer tokens than the schema needs, or a bad value token.
    """
    s = line.strip()
    type_name, _, rest = s.partition(" ")
    rest = rest.strip()
    schema: Optional[TypeSchema] = (schemas or {}).get(type_name)
    if schema is None:
        toks = rest.split()
        if not toks:
            raise FormatError("row without device: %r" % line)
        # Without a schema only a tier token marks where the device ends.
        tier_at = max((i for i, t in enumerate(toks) if t in TIER_TOKENS and i > 0), default=0)
        if tier_at:
            dev = rest.rsplit(None, len(toks) - tier_at)[0]
            tier, vals = toks[tier_at], toks[tier_at + 1 :]
        else:
            dev, tier, vals = toks[0], None, toks[1:]
        for v in vals:
            parse_value(v)
        return Row(type_name, dev, tier, {}, (), tuple(vals), line_no)

    # Decide the tier first: a tier token is always the token before the values.
    toks = rest.split()
    tier = None
    for candidate in TIER_TOKENS:
        entries = schema.fast_entries if candidate == TIER_FAST else schema.entries
        n = len(entries)
        if len(toks) >= n + 2 and toks[-(n + 1)] == candidate:
            tier = candidate
            break
    entries = schema.fast_entries if tier == TIER_FAST else schema.entries
    n = len(entries)
    keep = n + (1 if tier else 0)
    if len(toks) < keep + 1:
        raise FormatError(
            "%s row has %d tokens after the type, schema needs %d values: %r"
            % (type_name, len(toks), n, line)
        )
    head = rest.rsplit(None, keep)
    dev = head[0]
    vals = toks[len(toks) - n :] if n else []
    values = {e.key: parse_value(v) for e, v in zip(entries, vals)}
    return Row(type_name, dev, tier, values, tuple(entries), tuple(vals), line_no)


def iter_records(source: Source, *, strict: bool = False) -> Iterator[Record]:
    """Yield one record per meaningful line of ``source``.

    ``source`` is a path (compressed or not), an open text file, an iterable of lines, or a
    string holding a whole payload. With ``strict=False`` (default) a malformed line yields a
    :class:`Problem` and reading continues; with ``strict=True`` it raises
    :class:`FormatError`.
    """
    lines, owned = _lines(source)
    header: Optional[Header] = None
    in_header = False
    try:
        for line_no, raw in enumerate(lines, 1):
            line = raw.rstrip("\r\n")
            s = line.lstrip()
            if not s:
                continue
            c = s[0]
            try:
                if c == PROPERTY_CHAR:
                    if not in_header:
                        if header is not None:
                            yield header
                        header = Header()
                        header.line_no = line_no
                        in_header = True
                    _apply_property(header, s)  # type: ignore[arg-type]
                    continue
                if c == SCHEMA_CHAR:
                    if header is None:
                        header = Header()
                        header.line_no = line_no
                        in_header = True
                    ts = parse_schema_line(s)
                    header.schemas[ts.type] = ts
                    continue
                if in_header:
                    # The header block ends at the first non-`$`/`!` line.
                    in_header = False
                    yield header  # type: ignore[misc]
                if c.isdigit():
                    parts = s.split()
                    if len(parts) != 3:
                        raise FormatError("sample header needs 3 fields: %r" % line)
                    yield SampleStart(float(parts[0]), parts[1], parts[2], line_no)
                elif c == MARK_CHAR:
                    yield parse_mark(s)
                elif c.isalpha():
                    yield parse_row(s, header.schemas if header else None, line_no)
                else:
                    raise FormatError("unknown line kind: %r" % line)
            except ValueError as exc:  # FormatError, bad float/int
                if strict:
                    raise FormatError("line %d: %s" % (line_no, exc)) from exc
                yield Problem(line_no, line, str(exc))
        if in_header and header is not None:
            yield header
    finally:
        if owned is not None:
            owned.close()


def iter_samples(
    source: Source,
    *,
    strict: bool = False,
    problems: Optional[List[Problem]] = None,
    header: Optional[Header] = None,
) -> Iterator[Sample]:
    """Yield complete :class:`Sample` objects from ``source``.

    Args:
        source: See :func:`iter_records`.
        strict: Raise on the first malformed line instead of skipping it.
        problems: When given, skipped lines are appended here.
        header: Header to assume until the source provides its own, for a file that starts
            mid-stream (a ``current`` file is normally headed by ``$`` lines; a dumpfile or a
            spliced capture may not be).
    """
    cur: Optional[Sample] = None
    pending_marks: List[Mark] = []
    in_force = header
    for rec in _iter_records_with_header(source, strict, header):
        if isinstance(rec, Header):
            in_force = rec
        elif isinstance(rec, SampleStart):
            if cur is not None:
                yield cur
            cur = Sample(rec.time, rec.jobid, rec.host, in_force, rec.line_no)
            cur.marks.extend(pending_marks)
            pending_marks = []
        elif isinstance(rec, Mark):
            (cur.marks if cur is not None else pending_marks).append(rec)
        elif isinstance(rec, Row):
            if cur is None:
                rec_problem = Problem(rec.line_no, "", "row before any sample header")
                if strict:
                    raise FormatError(rec_problem.message)
                if problems is not None:
                    problems.append(rec_problem)
                continue
            cur.rows.append(rec)
        elif isinstance(rec, Problem) and problems is not None:
            problems.append(rec)
    if cur is not None:
        yield cur


def _iter_records_with_header(
    source: Source, strict: bool, header: Optional[Header]
) -> Iterator[Record]:
    """iter_records, but rows before the first in-file header use ``header``'s schemas."""
    if header is None:
        yield from iter_records(source, strict=strict)
        return
    seen_own = False
    for rec in iter_records(source, strict=strict):
        if isinstance(rec, Header):
            seen_own = True
        elif isinstance(rec, Row) and not seen_own and not rec.values and rec.type in header.schemas:
            line = " ".join((rec.type, rec.dev) + ((rec.tier,) if rec.tier else ()) + rec.raw)
            rec = parse_row(line, header.schemas, rec.line_no)
        yield rec


def read_file(path: PathLike, **kwargs) -> List[Sample]:
    """All samples of one file as a list (see :func:`iter_samples` for keyword arguments)."""
    return list(iter_samples(path, **kwargs))


def iter_host_archive(host_dir: PathLike, **kwargs) -> Iterator[Sample]:
    """Samples of every file in one ``<archive>/<host>/`` directory, oldest file first.

    listend appends to ``current`` and, on each ``$`` rotation, starts a new ``current``;
    every file also gets a digit name (the epoch of its first sample) as a hard link, so
    ``current`` usually shares its inode with the newest digit file. Each inode is read once.
    Digit names may carry a ``.gz`` / ``.bz2`` / ``.xz`` suffix; other names are skipped.
    The header of one file carries over to the next (``header=`` seeds the first).
    """
    picked = {}
    for name in os.listdir(host_dir):
        stem = name.split(".", 1)[0]
        if name != "current" and not stem.isdigit():
            continue
        p = os.path.join(str(host_dir), name)
        if not os.path.isfile(p):
            continue
        st = os.stat(p)
        order = int(stem) if stem.isdigit() else float("inf")
        inode = (st.st_dev, st.st_ino)
        if inode not in picked or order < picked[inode][0]:
            picked[inode] = (order, p)
    header: Optional[Header] = kwargs.pop("header", None)
    for _order, p in sorted(picked.values()):
        for sample in iter_samples(p, header=header, **kwargs):
            header = sample.header or header
            yield sample
