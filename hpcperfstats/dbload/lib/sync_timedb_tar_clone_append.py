"""
Append raw stats files to a daily tar by cloning their blocks into it.

Replaces ``tar -r``. Each member gets a pax header whose ``comment`` record
pads the header so the member data starts on a 4 KiB boundary; the raw file's
blocks are then shared into the tar with ``FICLONERANGE`` (reflink), so an
append costs metadata instead of a data copy. When the filesystem cannot
clone (other filesystem, no reflink) the data is copied by a ``dd``
subprocess under ``run_subprocess_with_progress`` (tar growth is the
progress metric; no growth for ``sync_ingest_stall_idle_s`` kills it and
rolls the append back), so a hung copy is noticed exactly like the old
``tar -r``. The clone ioctl and the commit fsync stay in-process: they are
metadata-sized, and a thread blocked in the kernel cannot be interrupted
from inside the process anyway. GNU ``tar`` and
``tarfile`` read the result as an ordinary pax archive (``comment`` is a
reader-ignored pax keyword).

Crash safety (a stop at any instruction loses nothing): the writer only
writes at or past the old logical end of the archive. Before it touches the
tar it durably records that end in ``<day>.tar.append_journal`` (fsync'd
temp, ``os.replace``, directory fsync). After the last member it writes the
end-of-archive blocks, fsyncs the tar and only then removes the journal (the
commit point). ``recover_interrupted_append`` undoes an uncommitted append
from the journal; it runs at orchestrator startup, at every daily-tar write
lock holder before it reads or rewrites the tar, and in the scratch sweep. It
truncates only a tar whose identity and pre-append tail hash match the
journal, so a journal left over a replaced tar never truncates it. A missing
tar is created as an ``.atomic.`` temp and hard-linked into place, so a crash
there leaves only a temp the scratch sweep removes.

Attributes:
  BLOCK: Tar block size in bytes.
  ALIGN: Data alignment for member data (filesystem block size for clone).
  TAR_EOF: The two zero blocks that end a tar archive.
  JOURNAL_SUFFIX: Suffix of the append journal next to a daily tar.
  CLONE_HEADER_BYTES: Per-member bytes a cloned append writes (pax header,
    alignment pad, 512 padding and the shared tail block), used by the
    space-pressure estimate.
  FICLONERANGE: Linux ioctl number for ``FICLONERANGE``.
  CLONE_FALLBACK_ERRNOS: ``FICLONERANGE`` errors that mean "cannot clone
    here" and fall back to a copy (any other error rolls the append back).
  TAIL_HASH_BYTES: Bytes before the old logical end hashed into the journal
    to prove the journal describes this tar.
  ZERO_TAIL_MAX: Largest tail past the logical end that is checked for zeros
    so a rollback can restore the exact old size.
  COPY_CHUNK: ``dd`` block size for the copy fallback.
  _ALIGN_PAD_MAX_TRIES: Bound on the header alignment loop.
  _CLONE_CAPABLE: Process-local cache of clone capability by
    ``(raw st_dev, tar dir st_dev)``.
  _OWNER_NAMES: Process-local uid/gid to user/group name cache.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import grp
import hashlib
import json
import os
import pwd
import stat
import struct
import tarfile
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from hpcperfstats.dbload.lib.print_utils import log_print

BLOCK = 512
ALIGN = 4096
TAR_EOF = bytes(2 * BLOCK)
JOURNAL_SUFFIX = ".append_journal"
CLONE_HEADER_BYTES = 8192
FICLONERANGE = 0x4020940D
CLONE_FALLBACK_ERRNOS = frozenset(
  {
    errno.EXDEV,
    errno.EOPNOTSUPP,
    errno.ENOTSUP,
    errno.EINVAL,
    errno.ENOTTY,
    errno.ENOSYS,
  }
)
TAIL_HASH_BYTES = 4096
ZERO_TAIL_MAX = 1 << 20
COPY_CHUNK = 8 << 20
_ALIGN_PAD_MAX_TRIES = 16
_CLONE_CAPABLE: dict[tuple[int, int], bool] = {}
_OWNER_NAMES: dict[tuple[str, int], str] = {}


@dataclass(frozen=True)
class CloneAppendResult:
  """
  Outcome of one ``clone_append`` call.

  Attributes:
    appended (int): Members written.
    cloned (int): Members whose data was reflink-cloned.
    copied (int): Members whose data was copied (no clone support).
    skipped (int): Paths skipped (vanished or not a regular file).
    write_s (float): Seconds from opening the tar to the commit.
    old_end (int): Logical end of the archive before the append.
    new_size (int): Tar size after the append.
  """

  appended: int = 0
  cloned: int = 0
  copied: int = 0
  skipped: int = 0
  write_s: float = 0.0
  old_end: int = 0
  new_size: int = 0


def journal_path(tar_path: str) -> str:
  """
  Return the append journal path that sits next to ``tar_path``.

  Args:
    tar_path (str): Daily ``.tar`` path.

  Returns:
    str: ``<tar_path>.append_journal``.

  Examples:
    >>> journal_path("/d/2026-07-31.tar")
    '/d/2026-07-31.tar.append_journal'
  """
  return f"{tar_path}{JOURNAL_SUFFIX}"


def _fsync_dir(path: str) -> None:
  """
  Flush a directory entry change (create, rename, unlink) to disk.

  Args:
    path (str): Directory to fsync.

  Returns:
    None

  Examples:
    >>> import tempfile
    >>> _fsync_dir(tempfile.gettempdir())
  """
  fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _write_durable(path: str, data: bytes) -> None:
  """
  Atomically and durably replace ``path`` with ``data``.

  Writes an ``.atomic.`` temp in the same directory (the scratch sweep's
  ``WRITE_TEMP_RE`` removes one a crash leaves), fsyncs it, renames it over
  ``path`` and fsyncs the directory, so after return the new content
  survives power loss and a reader sees either the old or the new file.

  Args:
    path (str): Destination file.
    data (bytes): Full new content.

  Returns:
    None

  Raises:
    OSError: Write, fsync or rename failed (the temp is removed).
    Exception: Any other failure (including KeyboardInterrupt) is re-raised
      after the temp is removed.

  Examples:
    >>> import os, tempfile
    >>> p = os.path.join(tempfile.mkdtemp(), "j")
    >>> _write_durable(p, b"{}")
    >>> open(p, "rb").read()
    b'{}'
  """
  parent = os.path.dirname(path) or "."
  fd, tmp = tempfile.mkstemp(
    prefix=".atomic.", suffix=os.path.basename(path), dir=parent
  )
  try:
    with os.fdopen(fd, "wb") as fh:
      fh.write(data)
      fh.flush()
      os.fsync(fh.fileno())
    os.replace(tmp, path)
  except BaseException:
    with contextlib.suppress(OSError):
      os.unlink(tmp)
    raise
  _fsync_dir(parent)


def _remove_durable(path: str) -> None:
  """
  Unlink ``path`` (if present) and fsync its directory.

  Args:
    path (str): File to remove.

  Returns:
    None

  Examples:
    >>> _remove_durable("/nonexistent/x.append_journal")
  """
  try:
    os.unlink(path)
  except FileNotFoundError:
    return
  _fsync_dir(os.path.dirname(path) or ".")


def _tail_sha256(fd: int, end: int) -> str:
  """
  Hash the ``TAIL_HASH_BYTES`` bytes before ``end`` in an open file.

  Args:
    fd (int): Open file descriptor.
    end (int): Offset the hashed range ends at.

  Returns:
    str: Hex sha256 of ``[max(0, end - TAIL_HASH_BYTES), end)``.

  Examples:
    >>> import os, tempfile
    >>> fd, p = tempfile.mkstemp()
    >>> len(_tail_sha256(fd, 0))
    64
  """
  start = max(0, end - TAIL_HASH_BYTES)
  return hashlib.sha256(os.pread(fd, end - start, start)).hexdigest()


def logical_end(fd: int, size: int) -> int:
  """
  Return the offset where the next member header belongs in an open tar.

  Walks member headers with ``tarfile`` (seeking past member data, so the
  cost is one header read per member) and returns the offset of the first
  end-of-archive block. GNU tar pads archives to a 10 KiB record, so the
  file size is not the logical end.

  Args:
    fd (int): Open tar descriptor (read access).
    size (int): Current file size (0 means an empty archive).

  Returns:
    int: Logical end of the archive, a multiple of 512.

  Raises:
    tarfile.TarError: Raised for an archive ``tarfile`` cannot walk (the
      caller fails the append; the existing open-tar repair owns it).

  Examples:
    >>> import os, tempfile
    >>> fd, p = tempfile.mkstemp()
    >>> logical_end(fd, 0)
    0
  """
  if size <= 0:
    return 0
  with (
    os.fdopen(os.dup(fd), "rb") as fh,
    tarfile.open(fileobj=fh, mode="r:") as tf,
  ):
    while tf.next() is not None:
      tf.members.clear()
    return int(tf.offset)


def _owner_name(kind: str, ident: int) -> str:
  """
  Return the user or group name for an id, cached per process.

  Args:
    kind (str): ``"u"`` for a uid, ``"g"`` for a gid.
    ident (int): Numeric id.

  Returns:
    str: Name, or ``""`` when the id has no entry.

  Examples:
    >>> _owner_name("u", 0)
    'root'
  """
  key = (kind, ident)
  if key not in _OWNER_NAMES:
    try:
      _OWNER_NAMES[key] = (
        pwd.getpwuid(ident).pw_name
        if kind == "u"
        else grp.getgrgid(ident).gr_name
      )
    except KeyError:
      _OWNER_NAMES[key] = ""
  return _OWNER_NAMES[key]


def build_member_header(name: str, st: os.stat_result, offset: int) -> bytes:
  """
  Build a pax member header whose data starts on an ``ALIGN`` boundary.

  The header carries name, mode, owner, mtime and size like ``tar --posix``
  (pax ``size`` above 8 GiB, pax ``path`` for long names) plus a ``comment``
  record grown until ``offset + len(header)`` is a multiple of ``ALIGN``.

  Args:
    name (str): Member name (path without the leading ``/``).
    st (os.stat_result): ``fstat`` of the open raw file.
    offset (int): Archive offset the header is written at (multiple of 512).

  Returns:
    bytes: Header blocks (pax extended header plus the ustar header).

  Raises:
    RuntimeError: Alignment did not converge (never expected).

  Examples:
    >>> import os
    >>> len(build_member_header("a", os.stat("/"), 0)) % 4096
    0
  """
  info = tarfile.TarInfo(name)
  info.size = st.st_size
  info.mode = stat.S_IMODE(st.st_mode)
  info.uid, info.gid = st.st_uid, st.st_gid
  info.uname = _owner_name("u", st.st_uid)
  info.gname = _owner_name("g", st.st_gid)
  info.mtime = st.st_mtime
  info.type = tarfile.REGTYPE
  pad = 0
  for _ in range(_ALIGN_PAD_MAX_TRIES):
    info.pax_headers = {"comment": "." * pad}
    hdr = info.tobuf(tarfile.PAX_FORMAT, "utf-8", "surrogateescape")
    need = -(offset + len(hdr)) % ALIGN
    if need == 0:
      return hdr
    pad += need
  raise RuntimeError(f"pax header alignment did not converge for {name}")


def _clone_range(src_fd: int, dst_fd: int, dst_off: int) -> None:
  """
  Share all of ``src_fd``'s blocks into ``dst_fd`` at ``dst_off``.

  Args:
    src_fd (int): Source (raw file) descriptor.
    dst_fd (int): Destination (tar) descriptor opened for writing.
    dst_off (int): Destination offset (multiple of the filesystem block).

  Returns:
    None

  Raises:
    OSError: The ioctl failed (``CLONE_FALLBACK_ERRNOS`` means copy instead).

  Examples:
    >>> _clone_range(-1, -1, 0)  # doctest: +SKIP
  """
  # struct file_clone_range {s64 src_fd; u64 src_offset, src_length, dest_offset}
  # src_length 0 = to the source EOF (the unaligned tail block is allowed).
  fcntl.ioctl(dst_fd, FICLONERANGE, struct.pack("=qQQQ", src_fd, 0, 0, dst_off))


def _copy_range(
  src_fd: int, tar_path: str, dst_fd: int, dst_off: int, size: int
) -> None:
  """
  Copy all ``size`` bytes of ``src_fd`` into the tar at ``dst_off`` with dd.

  Runs GNU ``dd`` under ``run_subprocess_with_progress`` so a copy that
  stops making progress is noticed and killed like the old ``tar -r``: the
  tar's size is the progress metric (``dd`` writes past its end), the SOP
  ``progress stage=tar_append_copy`` line reports it, and after
  ``sync_ingest_stall_idle_s`` without growth the process group is killed
  and ``ProgressIdleError`` propagates (the caller rolls the tar back). The
  source is read through ``/proc/<pid>/fd/<n>``, so ``dd`` copies exactly the
  file the writer opened and measured.

  Args:
    src_fd (int): Source (raw file) descriptor of this process.
    tar_path (str): Tar being appended (``dd`` output, progress path).
    dst_fd (int): Tar descriptor (used to verify the copied length).
    dst_off (int): Destination offset.
    size (int): Bytes to copy from source offset 0.

  Returns:
    None

  Raises:
    OSError: Raised for a non-zero ``dd`` exit or a short copy.
    ProgressIdleError: Raised when ``dd`` made no progress for the idle
      window.

  Examples:
    >>> _copy_range(-1, "/nonexistent.tar", -1, 0, 0)
  """
  if size <= 0:
    return
  from hpcperfstats.dbload.lib.sync_timedb_progress_io import (
    run_subprocess_with_progress,
  )

  result = run_subprocess_with_progress(
    [
      "dd",
      f"if=/proc/{os.getpid()}/fd/{src_fd}",
      f"of={tar_path}",
      f"bs={COPY_CHUNK}",
      f"seek={dst_off}",
      f"count={size}",
      "iflag=count_bytes",
      "oflag=seek_bytes",
      "conv=notrunc",
      "status=none",
    ],
    progress_path=tar_path,
    stage="tar_append_copy",
    metric="bytes",
    # One dd per member: the default 1 s poll would add ~1 s per member.
    poll_s=0.05,
  )
  if result.returncode != 0:
    raise OSError(
      errno.EIO,
      f"dd copy failed rc={result.returncode}: {(result.stderr or '').strip()}",
    )
  if os.fstat(dst_fd).st_size < dst_off + size:
    raise OSError(errno.EIO, f"short copy into {tar_path} at {dst_off}")


def clone_capable(tar_path: str, sample_path: str | None = None) -> bool:
  """
  Return True when appends to ``tar_path`` will clone instead of copy.

  Probes once per ``(raw device, tar directory device)`` pair by cloning one
  block between two unnamed ``O_TMPFILE`` files in the tar directory (no
  residue on any exit) and caches the answer for the process. A raw file on
  a different device than the tar can never clone.

  Args:
    tar_path (str): Daily tar (need not exist; its directory must).
    sample_path (str | None): A raw file of the batch, or None to skip the
      same-device check.

  Returns:
    bool: True when cloning works; False when it does not or is unknown.

  Examples:
    >>> clone_capable("/nonexistent/dir/2026-01-01.tar")
    False
  """
  tar_dir = os.path.dirname(os.path.abspath(tar_path)) or "."
  try:
    dir_dev = os.stat(tar_dir).st_dev
    src_dev = os.stat(sample_path).st_dev if sample_path else dir_dev
  except OSError:
    return False
  key = (src_dev, dir_dev)
  if src_dev != dir_dev:
    return False
  if key not in _CLONE_CAPABLE:
    _CLONE_CAPABLE[key] = _probe_clone(tar_dir)
  return _CLONE_CAPABLE[key]


def _probe_clone(directory: str) -> bool:
  """
  Return True when ``FICLONERANGE`` works between files in ``directory``.

  Args:
    directory (str): Directory on the filesystem to probe.

  Returns:
    bool: Clone result; False when ``O_TMPFILE`` or the clone fails.

  Examples:
    >>> _probe_clone("/nonexistent")
    False
  """
  fds: list[int] = []
  try:
    for _ in range(2):
      fds.append(os.open(directory, os.O_TMPFILE | os.O_RDWR, 0o600))
    os.pwrite(fds[0], b"\1" * ALIGN, 0)
    _clone_range(fds[0], fds[1], 0)
    return True
  except OSError, AttributeError:
    return False
  finally:
    for fd in fds:
      os.close(fd)


def note_clone_capability(
  tar_path: str, sample_path: str, cloned: int, copied: int
) -> None:
  """
  Record what an append actually saw so the estimate follows reality.

  A batch that wrote no data (only empty files) says nothing and is ignored;
  any copied member marks the pair as not clone-capable.

  Args:
    tar_path (str): Daily tar the append wrote.
    sample_path (str): A raw file of that append.
    cloned (int): Members whose data the append cloned.
    copied (int): Members whose data the append copied.

  Returns:
    None

  Examples:
    >>> note_clone_capability("/nonexistent/x.tar", "/nonexistent/y", 0, 1)
  """
  if not (cloned or copied):
    return
  with contextlib.suppress(OSError):
    tar_dir = os.path.dirname(os.path.abspath(tar_path)) or "."
    key = (os.stat(sample_path).st_dev, os.stat(tar_dir).st_dev)
    _CLONE_CAPABLE[key] = copied == 0


def _zero_tail(fd: int, start: int, end: int) -> bool:
  """
  Return True when ``[start, end)`` is all zero bytes (and small enough).

  Args:
    fd (int): Open descriptor.
    start (int): Range start.
    end (int): Range end.

  Returns:
    bool: True when the range is at most ``ZERO_TAIL_MAX`` bytes of zeros.

  Examples:
    >>> import os, tempfile
    >>> fd, p = tempfile.mkstemp()
    >>> _zero_tail(fd, 0, 0)
    True
  """
  if end - start > ZERO_TAIL_MAX:
    return False
  return not any(os.pread(fd, end - start, start))


def _restore_old_end(fd: int, old_end: int, old_size: int) -> None:
  """
  Return an open tar to its content before an append, then fsync it.

  The bytes below ``old_end`` were never written by the append. When the
  journal recorded an all-zero tail (``old_size`` past ``old_end``) the
  exact old size is restored; otherwise the end-of-archive blocks are
  rewritten at ``old_end``.

  Args:
    fd (int): Tar descriptor opened read-write.
    old_end (int): Logical end before the append.
    old_size (int): Size to restore, or 0 to end at ``old_end + 1024``.

  Returns:
    None

  Examples:
    >>> import os, tempfile
    >>> fd, p = tempfile.mkstemp()
    >>> _restore_old_end(fd, 0, 1024)
    >>> os.path.getsize(p)
    1024
  """
  os.ftruncate(fd, old_end)
  if old_size > old_end:
    os.ftruncate(fd, old_size)
  else:
    os.pwrite(fd, TAR_EOF, old_end)
  os.fsync(fd)


def _read_journal(path: str) -> dict[str, Any] | None:
  """
  Load an append journal, or None when it is absent.

  Args:
    path (str): Journal path.

  Returns:
    dict[str, Any] | None: Parsed journal, None when missing.

  Raises:
    ValueError: The journal exists but is not a valid journal object.

  Examples:
    >>> _read_journal("/nonexistent/x.append_journal") is None
    True
  """
  try:
    with open(path, "rb") as fh:
      raw = fh.read()
  except FileNotFoundError:
    return None
  data = json.loads(raw.decode("utf-8"))
  for key in ("old_end", "old_size", "st_dev", "st_ino", "tail_sha256"):
    if key not in data:
      raise ValueError(f"journal missing {key}")
  return data


def recover_interrupted_append(
  tar_path: str, *, log_fn: Any = log_print
) -> str:
  """
  Undo an append that stopped before its commit; caller holds the write lock.

  Args:
    tar_path (str): Daily tar whose journal to apply.
    log_fn (Any): Logger callable, or None for silence.

  Returns:
    str: ``none`` (no journal), ``orphan`` (journal without a tar, removed),
    ``stale`` (journal for a different tar or tail, removed, tar untouched),
    ``corrupt`` (unreadable journal moved to ``.corrupt.<ts>``, tar
    untouched) or ``recovered`` (tar truncated back to its old end).

  Raises:
    OSError: Truncating or fsyncing the tar failed; the journal stays for
      the next attempt.

  Examples:
    >>> recover_interrupted_append("/nonexistent/2026-01-01.tar")
    'none'
  """
  jpath = journal_path(tar_path)
  try:
    journal = _read_journal(jpath)
  except (ValueError, UnicodeDecodeError, OSError) as exc:
    aside = f"{jpath}.corrupt.{int(time.time())}"
    os.replace(jpath, aside)
    if log_fn:
      log_fn(
        f"ERROR: archive append journal unreadable tar={tar_path} "
        f"moved={aside} err={exc}; tar left for the readability repair",
        flush=True,
      )
    return "corrupt"
  if journal is None:
    return "none"
  try:
    fd = os.open(tar_path, os.O_RDWR)
  except FileNotFoundError:
    _remove_durable(jpath)
    return "orphan"
  try:
    st = os.fstat(fd)
    old_end = int(journal["old_end"])
    if (
      (st.st_dev, st.st_ino) != (journal["st_dev"], journal["st_ino"])
      or st.st_size < old_end
      or _tail_sha256(fd, old_end) != journal["tail_sha256"]
    ):
      _remove_durable(jpath)
      if log_fn:
        log_fn(
          f"WARNING: archive append journal stale tar={tar_path} "
          f"old_end={old_end}; tar untouched",
          flush=True,
        )
      return "stale"
    _restore_old_end(fd, old_end, int(journal["old_size"]))
  finally:
    os.close(fd)
  _remove_durable(jpath)
  if log_fn:
    log_fn(
      f"WARNING: archive append recovered tar={tar_path} old_end={old_end} "
      f"dropped_bytes={st.st_size - max(old_end, int(journal['old_size']))}",
      flush=True,
    )
  return "recovered"


def recover_all_interrupted_appends(
  daily_dir: str, *, log_fn: Any = log_print
) -> int:
  """
  Apply every leftover append journal in ``daily_dir`` (startup recovery).

  Takes each tar's write lock without waiting; a contended tar is left for
  its next lock holder or the scratch sweep, which recover it first.

  Args:
    daily_dir (str): Daily archive directory.
    log_fn (Any): Logger callable.

  Returns:
    int: Journals applied (``recovered``, ``stale``, ``orphan``, ``corrupt``).

  Examples:
    >>> recover_all_interrupted_appends("/nonexistent")
    0
  """
  from hpcperfstats.dbload.lib.file_locking import try_file_write_lock

  try:
    names = sorted(os.listdir(daily_dir))
  except OSError:
    return 0
  done = 0
  for name in names:
    if not name.endswith(".tar" + JOURNAL_SUFFIX):
      continue
    tar_path = os.path.join(daily_dir, name[: -len(JOURNAL_SUFFIX)])
    try:
      with try_file_write_lock(tar_path):
        done += recover_interrupted_append(tar_path, log_fn=log_fn) != "none"
    except (TimeoutError, OSError) as exc:
      if log_fn:
        log_fn(
          f"WARNING: archive append recovery deferred tar={tar_path} err={exc}",
          flush=True,
        )
  return done


@contextlib.contextmanager
def _open_sources(
  paths: Any,
) -> Iterator[list[tuple[str, int, os.stat_result]]]:
  """
  Open each raw path read-only; yield ``(path, fd, stat)`` for regular files.

  Args:
    paths (Any): Absolute or relative raw file paths.

  Yields:
    Iterator[list[tuple[str, int, os.stat_result]]]: Opened regular files
    (vanished paths, symlinks and non-regular files are left out).

  Examples:
    >>> with _open_sources(["/nonexistent"]) as got:
    ...   got
    []
  """
  opened: list[tuple[str, int, os.stat_result]] = []
  try:
    for path in paths:
      try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
      except OSError:
        continue
      st = os.fstat(fd)
      if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        continue
      opened.append((os.path.abspath(path), fd, st))
    yield opened
  finally:
    for _, fd, _ in opened:
      os.close(fd)


def _write_members(
  fd: int,
  tar_path: str,
  offset: int,
  sources: list[tuple[str, int, os.stat_result]],
) -> tuple[int, int, int]:
  """
  Write every member at ``offset`` and the end-of-archive blocks after them.

  Args:
    fd (int): Tar descriptor opened read-write.
    tar_path (str): Path of that descriptor (copy-fallback ``dd`` output).
    offset (int): Logical end to start at.
    sources (list[tuple[str, int, os.stat_result]]): Opened raw files.

  Returns:
    tuple[int, int, int]: ``(new_size, cloned, copied)``.

  Raises:
    OSError: A header or data write failed (caller rolls back).
    Exception: A clone error outside ``CLONE_FALLBACK_ERRNOS`` (for example
      ENOSPC) is re-raised for the caller's rollback.

  Examples:
    >>> _write_members(-1, "/nonexistent.tar", 0, [])  # doctest: +SKIP
  """
  cloned = copied = 0
  can_clone = True
  for path, src_fd, st in sources:
    hdr = build_member_header(path.lstrip("/"), st, offset)
    os.pwrite(fd, hdr, offset)
    data_off = offset + len(hdr)
    size = st.st_size
    if size and can_clone:
      try:
        _clone_range(src_fd, fd, data_off)
        cloned += 1
      except OSError as exc:
        if exc.errno not in CLONE_FALLBACK_ERRNOS:
          raise
        can_clone = False
    if size and not can_clone:
      _copy_range(src_fd, tar_path, fd, data_off, size)
      copied += 1
    end = data_off + size
    offset = end + (-end % BLOCK)
    if offset > end:
      os.pwrite(fd, bytes(offset - end), end)
  os.pwrite(fd, TAR_EOF, offset)
  os.ftruncate(fd, offset + len(TAR_EOF))
  return offset + len(TAR_EOF), cloned, copied


def _create_new_tar(
  tar_path: str, sources: list[tuple[str, int, os.stat_result]]
) -> tuple[int, int, int]:
  """
  Build a new tar in an ``.atomic.`` temp and hard-link it into place.

  Args:
    tar_path (str): Daily tar that does not exist yet.
    sources (list[tuple[str, int, os.stat_result]]): Opened raw files.

  Returns:
    tuple[int, int, int]: ``(new_size, cloned, copied)``.

  Raises:
    FileExistsError: Another writer created the tar (never under the lock).
    OSError: Any write failed; the temp is removed.

  Examples:
    >>> _create_new_tar("/nonexistent/x.tar", [])  # doctest: +SKIP
  """
  parent = os.path.dirname(tar_path) or "."
  fd, tmp = tempfile.mkstemp(
    prefix=".atomic.", suffix=os.path.basename(tar_path), dir=parent
  )
  try:
    result = _write_members(fd, tmp, 0, sources)
    os.fsync(fd)
    os.link(tmp, tar_path)
  finally:
    os.close(fd)
    with contextlib.suppress(OSError):
      os.unlink(tmp)
  _fsync_dir(parent)
  return result


def clone_append(
  tar_path: str,
  paths: Any,
  *,
  log_fn: Any = log_print,
) -> CloneAppendResult:
  """
  Append ``paths`` to ``tar_path``; the caller holds the tar's write lock.

  Recovers a leftover journal first, journals the old logical end, writes
  each member (pax header, cloned or copied data, 512 padding), writes the
  end-of-archive blocks, fsyncs the tar and drops the journal. Any failure
  before the commit truncates the tar back to its old content; if that
  rollback itself fails the journal stays for recovery.

  Args:
    tar_path (str): Daily ``.tar`` (created when missing).
    paths (Any): Raw files to append, in order.
    log_fn (Any): Logger callable.

  Returns:
    CloneAppendResult: Counts, timing and offsets.

  Raises:
    OSError: Raised for a write failure (ENOSPC, EIO) after rollback.
    ProgressIdleError: Raised when the copy fallback stalled; the tar was
      rolled back.
    tarfile.TarError: Raised for an existing tar ``tarfile`` cannot walk;
      nothing was written.
    Exception: Any failure between the journal and the commit (including a
      signal) is re-raised after the tar was rolled back.

  Examples:
    >>> clone_append("/nonexistent/2026-01-01.tar", []).appended
    0
  """
  started = time.monotonic()
  paths = list(paths)
  with _open_sources(paths) as sources:
    skipped = len(paths) - len(sources)
    if not sources:
      return CloneAppendResult(skipped=skipped)
    recover_interrupted_append(tar_path, log_fn=log_fn)
    try:
      fd = os.open(tar_path, os.O_RDWR)
    except FileNotFoundError:
      new_size, cloned, copied = _create_new_tar(tar_path, sources)
      note_clone_capability(tar_path, sources[0][0], cloned, copied)
      return CloneAppendResult(
        appended=len(sources),
        cloned=cloned,
        copied=copied,
        skipped=skipped,
        write_s=time.monotonic() - started,
        new_size=new_size,
      )
    try:
      st = os.fstat(fd)
      old_end = logical_end(fd, st.st_size)
      zero = _zero_tail(fd, old_end, st.st_size)
      jpath = journal_path(tar_path)
      _write_durable(
        jpath,
        json.dumps(
          {
            "v": 1,
            "old_end": old_end,
            "old_size": st.st_size if zero else 0,
            "st_dev": st.st_dev,
            "st_ino": st.st_ino,
            "tail_sha256": _tail_sha256(fd, old_end),
          },
          sort_keys=True,
        ).encode("utf-8"),
      )
      try:
        # XFS clones a source's partial last block only at the destination
        # EOF, so the record padding past the logical end must go first.
        os.ftruncate(fd, old_end)
        new_size, cloned, copied = _write_members(
          fd, tar_path, old_end, sources
        )
        os.fsync(fd)
      except BaseException:
        _restore_old_end(fd, old_end, st.st_size if zero else 0)
        _remove_durable(jpath)
        raise
    finally:
      os.close(fd)
    _remove_durable(jpath)
    note_clone_capability(tar_path, sources[0][0], cloned, copied)
  return CloneAppendResult(
    appended=len(sources),
    cloned=cloned,
    copied=copied,
    skipped=skipped,
    write_s=time.monotonic() - started,
    old_end=old_end,
    new_size=new_size,
  )
