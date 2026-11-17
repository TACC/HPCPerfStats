"""Codon compile unit for the update_metrics columnar kernels (``--lib``).

Not imported under CPython: ``@export`` and ``Ptr`` are Codon constructs.
Built by ``build_codon_metrics_ext.sh`` (Dockerfile ``codon-build`` stage)
into ``libhpcperfstats_codon_metrics_kernel.so`` and wrapped by
``pyext_metrics_init.c`` (``Py_MOD_GIL_NOT_USED``).

Every kernel reads and writes caller-allocated buffers (NumPy arrays owned by
CPython). Nothing here allocates per row, so metrics-pool threads running
different jobs do not meet on Boehm's allocation lock (plan decision D13).
The only heap use is the per-column distinct-name dictionary in the COPY
decoder, which grows once per new host/type/event/dev name.

Exports (C ABI, all ``int`` results are row/group counts or negative codes):

* ``copy_count_rows_c`` — pass 1 over a PostgreSQL ``COPY … (FORMAT BINARY)``
  host_data stream: tuple count or ``ERR_*``.
* ``copy_decode_c`` — pass 2: numeric columns plus chunk-local text ids and
  the distinct-name table (offset/length into the stream).
* ``group_reduce_c`` — count / sum / max per int64 key over masked rows,
  groups sorted by key (stable LSD radix sort in caller scratch), summation
  in row order (``np.unique`` + ``np.add.at`` parity).
* ``residual_collapse_c`` - shared-node residual collapse per int64 key:
  row-order SUM of hardware-device rows, else MAX of ``j:`` rows
  (``segments._np_residual_collapse`` parity).
* ``grid_mean_c`` — mean per dense cell, NaN where no finite sample.
* ``join_sorted_c`` — inner join of two sorted unique key arrays.
* ``sentinel_samples_c`` - per-sample valid / bridged / gap states of a
  value-sentinel fold: row-order sums per ``(host, time)`` key, last gap or
  bridge code, first row (``sentinel_profile._np_sentinel_samples`` parity).
* ``sentinel_fold_c`` - per-host 5-minute bucket means of covered samples,
  gap flags, first-bucket drop, interior fill and per-host state counts
  (``sentinel_profile._np_sentinel_fold`` parity).
* ``fill_interior_gaps_c`` - linear fill of interior gap-bucket runs
  (``sentinel_profile._np_fill_interior_gaps`` parity).
"""

# ruff: noqa: F821  (Codon builtins: Ptr, u8, u64, i32, float32, export)

ERR_SIGNATURE = -1
ERR_FIELD_COUNT = -2
ERR_NULL_TIME = -3
ERR_NAME_CAP = -4
ERR_ROW_COUNT = -5
ERR_TRUNCATED = -6

COPY_HEADER_LEN = 19
# b"PGCOPY\n\xff\r\n\x00"
COPY_SIGNATURE = (80, 71, 67, 79, 80, 89, 10, 255, 13, 10, 0)
PG_EPOCH_OFFSET_US = 946684800000000

NAME_COLS = 4


def _be_i16(p: Ptr[u8], off: int) -> int:
  v = (int(p[off]) << 8) | int(p[off + 1])
  if v >= 32768:
    v -= 65536
  return v


def _be_i32(p: Ptr[u8], off: int) -> int:
  v = (
    (int(p[off]) << 24)
    | (int(p[off + 1]) << 16)
    | (int(p[off + 2]) << 8)
    | int(p[off + 3])
  )
  if v >= 2147483648:
    v -= 4294967296
  return v


def _be_i64(p: Ptr[u8], off: int) -> int:
  v = 0
  for i in range(8):
    v = (v << 8) | int(p[off + i])
  return v


def _be_f32(p: Ptr[u8], off: int) -> float:
  bits = u32(
    (int(p[off]) << 24)
    | (int(p[off + 1]) << 16)
    | (int(p[off + 2]) << 8)
    | int(p[off + 3])
  )
  f = Ptr[float32](__ptr__(bits).as_byte())[0]
  return float(f)


def _signature_ok(p: Ptr[u8], n: int) -> bool:
  if n < COPY_HEADER_LEN:
    return False
  for i in range(11):  # noqa: SIM110 - plain loop keeps the Codon unit simple
    if int(p[i]) != COPY_SIGNATURE[i]:
      return False
  return True


def _header_end(p: Ptr[u8], n: int) -> int:
  ext = _be_i32(p, 15)
  if ext < 0:
    ext = 0
  return COPY_HEADER_LEN + ext


@export
def copy_count_rows_c(buf: Ptr[u8], n: int, has_owned: int) -> int:
  """Count tuples in a binary COPY stream without decoding them."""
  if not _signature_ok(buf, n):
    return ERR_SIGNATURE
  off = _header_end(buf, n)
  expected = 9 if has_owned != 0 else 8
  rows = 0
  while off + 2 <= n:
    nf = _be_i16(buf, off)
    off += 2
    if nf == -1:
      break
    if nf != expected:
      return ERR_FIELD_COUNT
    for _f in range(nf):
      if off + 4 > n:
        return ERR_TRUNCATED
      ln = _be_i32(buf, off)
      off += 4
      if ln > 0:
        off += ln
    if off > n:
      return ERR_TRUNCATED
    rows += 1
  return rows


def _intern(
  table: Dict[str, int],
  buf: Ptr[u8],
  off: int,
  ln: int,
  col: int,
  name_cap: int,
  n_names: Ptr[int],
  out_name_col: Ptr[int],
  out_name_off: Ptr[int],
  out_name_len: Ptr[int],
) -> int:
  key = str(buf + off, ln)
  got = table.get(key, -1)
  if got >= 0:
    return got
  idx = n_names[0]
  if idx >= name_cap:
    return ERR_NAME_CAP
  out_name_col[idx] = col
  out_name_off[idx] = off
  out_name_len[idx] = ln
  n_names[0] = idx + 1
  table[key] = idx
  return idx


@export
def copy_decode_c(
  buf: Ptr[u8],
  n: int,
  has_owned: int,
  nrows: int,
  out_time: Ptr[int],
  out_host: Ptr[i32],
  out_type: Ptr[i32],
  out_event: Ptr[i32],
  out_dev: Ptr[i32],
  out_owned: Ptr[u8],
  out_value: Ptr[float],
  out_arc: Ptr[float],
  out_delta: Ptr[float],
  name_cap: int,
  out_name_col: Ptr[int],
  out_name_off: Ptr[int],
  out_name_len: Ptr[int],
) -> int:
  """Decode a binary COPY stream into caller buffers of ``nrows`` entries.

  Text ids are indexes into the distinct-name table written to
  ``out_name_*`` (one table for the four text columns; ``out_name_col`` says
  which column each entry belongs to). Returns the number of distinct names
  or a negative ``ERR_*`` code.
  """
  if not _signature_ok(buf, n):
    return ERR_SIGNATURE
  off = _header_end(buf, n)
  expected = 9 if has_owned != 0 else 8
  tables = [Dict[str, int]() for _c in range(NAME_COLS)]
  n_names = 0
  n_names_ptr = __ptr__(n_names)
  nan = float("nan")
  row = 0
  while off + 2 <= n:
    nf = _be_i16(buf, off)
    off += 2
    if nf == -1:
      break
    if nf != expected:
      return ERR_FIELD_COUNT
    if row >= nrows:
      return ERR_ROW_COUNT
    ln = _be_i32(buf, off)
    off += 4
    if ln != 8:
      return ERR_NULL_TIME
    out_time[row] = _be_i64(buf, off) + PG_EPOCH_OFFSET_US
    off += 8
    for col in range(NAME_COLS):
      ln = _be_i32(buf, off)
      off += 4
      if ln < 0:
        ln = 0
      if off + ln > n:
        return ERR_TRUNCATED
      ident = _intern(
        tables[col],
        buf,
        off,
        ln,
        col,
        name_cap,
        n_names_ptr,
        out_name_col,
        out_name_off,
        out_name_len,
      )
      if ident < 0:
        return ident
      off += ln
      if col == 0:
        out_host[row] = i32(ident)
      elif col == 1:
        out_type[row] = i32(ident)
      elif col == 2:
        out_event[row] = i32(ident)
      else:
        out_dev[row] = i32(ident)
    for col in range(3):
      ln = _be_i32(buf, off)
      off += 4
      val = nan
      if ln == 4:
        if off + 4 > n:
          return ERR_TRUNCATED
        val = _be_f32(buf, off)
      if ln > 0:
        off += ln
      if col == 0:
        out_value[row] = val
      elif col == 1:
        out_arc[row] = val
      else:
        out_delta[row] = val
    if has_owned != 0:
      ln = _be_i32(buf, off)
      off += 4
      own = u8(0)
      if ln == 4:
        if off + 4 > n:
          return ERR_TRUNCATED
        if _be_i32(buf, off) != 0:
          own = u8(1)
      if ln > 0:
        off += ln
      out_owned[row] = own
    else:
      out_owned[row] = u8(1)
    if off > n:
      return ERR_TRUNCATED
    row += 1
  if row != nrows:
    return ERR_ROW_COUNT
  return n_names_ptr[0]


RADIX_BITS = 16
RADIX_BUCKETS = 1 << RADIX_BITS
RADIX_MASK = u64(RADIX_BUCKETS - 1)
SIGN_FLIP = u64(1) << u64(63)


def _radix_sort_pairs(
  m: int,
  k: Ptr[u64],
  v: Ptr[int],
  kt: Ptr[u64],
  vt: Ptr[int],
  cnt: Ptr[int],
) -> int:
  """LSD radix sort of ``(k, v)`` by ``k`` with 16-bit digits.

  Stable, so rows with equal keys keep their input order. Passes whose
  digit is constant across every key are skipped. Returns 0 when the sorted
  pairs are in ``(k, v)`` and 1 when they ended in ``(kt, vt)``.
  """
  if m < 2:
    return 0
  hi = u64(0)
  for i in range(m):
    if k[i] > hi:
      hi = k[i]
  src_k = k
  src_v = v
  dst_k = kt
  dst_v = vt
  where = 0
  shift = u64(0)
  while True:
    if (hi >> shift) == u64(0) and shift > u64(0):
      break
    for b in range(RADIX_BUCKETS):
      cnt[b] = 0
    for i in range(m):
      cnt[int((src_k[i] >> shift) & RADIX_MASK)] += 1
    constant = False
    for b in range(RADIX_BUCKETS):
      if cnt[b] == m:
        constant = True
        break
    if not constant:
      total = 0
      for b in range(RADIX_BUCKETS):
        c = cnt[b]
        cnt[b] = total
        total += c
      for i in range(m):
        d = int((src_k[i] >> shift) & RADIX_MASK)
        pos = cnt[d]
        cnt[d] = pos + 1
        dst_k[pos] = src_k[i]
        dst_v[pos] = src_v[i]
      tmp_k = src_k
      src_k = dst_k
      dst_k = tmp_k
      tmp_v = src_v
      src_v = dst_v
      dst_v = tmp_v
      where = 1 - where
    shift += u64(RADIX_BITS)
    if shift >= u64(64):
      break
  return where


def _sort_masked_rows(
  n: int,
  keys: Ptr[int],
  has_mask: int,
  mask: Ptr[u8],
  scratch: Ptr[int],
) -> Tuple[int, int]:
  """Stable radix sort of the masked rows' ``(key, row)`` pairs in scratch.

  ``scratch`` needs ``4 * n + RADIX_BUCKETS`` entries. Returns ``(m, where)``:
  the masked row count and which half (0: ``scratch[0:2n]``, 1:
  ``scratch[2n:4n]``) holds the sorted keys then row indexes.
  """
  k = Ptr[u64](scratch.as_byte())
  v = scratch + n
  kt = Ptr[u64]((scratch + 2 * n).as_byte())
  vt = scratch + 3 * n
  cnt = scratch + 4 * n
  m = 0
  negative = False
  for i in range(n):
    if has_mask == 0 or mask[i] != u8(0):
      key = keys[i]
      if key < 0:
        negative = True
      k[m] = u64(key)
      v[m] = i
      m += 1
  if m == 0:
    return 0, 0
  if negative:
    for i in range(m):
      k[i] = k[i] ^ SIGN_FLIP
  return m, _radix_sort_pairs(m, k, v, kt, vt, cnt)


@export
def group_reduce_c(
  n: int,
  keys: Ptr[int],
  values: Ptr[float],
  has_mask: int,
  mask: Ptr[u8],
  scratch: Ptr[int],
  out_keys: Ptr[int],
  out_cnt: Ptr[int],
  out_sum: Ptr[float],
  out_max: Ptr[float],
) -> int:
  """Count / sum / max per key over rows with ``mask != 0`` (or all rows).

  Groups come out sorted by key; within a group values are added in row
  order from ``0.0`` so sums match ``np.add.at`` bit for bit (a lone
  ``-0.0`` sums to ``+0.0``) and a NaN value poisons the group's sum and
  max like ``np.maximum.at``. Output buffers need ``n`` entries;
  ``scratch`` needs ``4 * n + RADIX_BUCKETS`` entries. Returns the group
  count.
  """
  m, where = _sort_masked_rows(n, keys, has_mask, mask, scratch)
  half = scratch + 2 * n * where
  sk = Ptr[u64](half.as_byte())
  sv = half + n
  g = -1
  cur = u64(0)
  for j in range(m):
    r = sv[j]
    ku = sk[j]
    val = values[r]
    if g < 0 or ku != cur:
      g += 1
      cur = ku
      out_keys[g] = keys[r]
      out_cnt[g] = 1
      out_sum[g] = 0.0 + val
      out_max[g] = val
    else:
      out_cnt[g] += 1
      out_sum[g] += val
      mx = out_max[g]
      # np.maximum.at parity: NaN poisons the group maximum.
      if val != val or (mx == mx and val > mx):
        out_max[g] = val
  return g + 1


@export
def residual_collapse_c(
  n: int,
  keys: Ptr[int],
  dev: Ptr[int],
  values: Ptr[float],
  mask: Ptr[u8],
  scratch: Ptr[int],
  out_keys: Ptr[int],
  out_val: Ptr[float],
) -> int:
  """Per key over rows with ``mask != 0``: hardware SUM else ``j:`` MAX.

  Rows with ``dev >= 0`` are hardware devices; their values are added in
  row order from ``0.0`` (``np.add.at`` parity). A key without a hardware
  row takes the maximum of its ``dev < 0`` (``j:``) rows, NaN poisoning it
  like ``np.maximum.at``. Keys come out sorted. Output buffers need ``n``
  entries; ``scratch`` needs ``4 * n + RADIX_BUCKETS`` entries. Returns the
  key count.
  """
  m, where = _sort_masked_rows(n, keys, 1, mask, scratch)
  half = scratch + 2 * n * where
  sk = Ptr[u64](half.as_byte())
  sv = half + n
  g = -1
  cur = u64(0)
  hw_seen = False
  j_seen = False
  hw_sum = 0.0
  j_max = 0.0
  for j in range(m + 1):
    if j == m or g < 0 or sk[j] != cur:
      if g >= 0:
        out_val[g] = hw_sum if hw_seen else j_max
      if j == m:
        break
      g += 1
      cur = sk[j]
      out_keys[g] = keys[sv[j]]
      hw_seen = False
      j_seen = False
      hw_sum = 0.0
    r = sv[j]
    val = values[r]
    if dev[r] >= 0:
      hw_seen = True
      hw_sum += val
    elif not j_seen:
      j_seen = True
      j_max = val
    elif val != val or (j_max == j_max and val > j_max):
      j_max = val
  return g + 1


@export
def grid_mean_c(
  n: int,
  cells: Ptr[int],
  values: Ptr[float],
  size: int,
  cnt_scratch: Ptr[int],
  out_grid: Ptr[float],
) -> int:
  """Mean of finite ``values`` per cell; NaN for cells without one.

  ``out_grid`` and ``cnt_scratch`` hold ``size`` entries. Returns the
  number of rows that landed in a cell (``-1`` for an out-of-range cell).
  """
  for c in range(size):
    out_grid[c] = 0.0
    cnt_scratch[c] = 0
  used = 0
  for i in range(n):
    v = values[i]
    if v != v:
      continue
    c = cells[i]
    if c < 0 or c >= size:
      return -1
    out_grid[c] += v
    cnt_scratch[c] += 1
    used += 1
  nan = float("nan")
  for c in range(size):
    if cnt_scratch[c] > 0:
      out_grid[c] = out_grid[c] / float(cnt_scratch[c])
    else:
      out_grid[c] = nan
  return used


@export
def join_sorted_c(
  nl: int,
  left: Ptr[int],
  nr: int,
  right: Ptr[int],
  out_li: Ptr[int],
  out_ri: Ptr[int],
) -> int:
  """Two-pointer inner join of sorted unique keys; returns the hit count."""
  i = 0
  j = 0
  h = 0
  while i < nl and j < nr:
    a = left[i]
    b = right[j]
    if a == b:
      out_li[h] = i
      out_ri[h] = j
      h += 1
      i += 1
      j += 1
    elif a < b:
      i += 1
    else:
      j += 1
  return h


# Literal mirrors of ``sentinel_profile.SAMPLE_*`` (locked by
# ``test_codon_metrics_bake_contract.py``).
SAMPLE_VALID = 0
SAMPLE_BRIDGED = 1
SAMPLE_GAP = 2
POS_INF = float("inf")


@export
def sentinel_samples_c(
  n: int,
  keys: Ptr[int],
  valid: Ptr[u8],
  bridged: Ptr[u8],
  gap_row: Ptr[u8],
  code: Ptr[int],
  eff: Ptr[float],
  scratch: Ptr[int],
  out_first: Ptr[int],
  out_sum: Ptr[float],
  out_status: Ptr[int],
  out_code: Ptr[int],
) -> int:
  """Per-sample states of one sentinel fold, one sample per distinct key.

  Samples come out sorted by key. A sample's sum adds ``eff`` of its valid
  or bridged rows in row order from ``0.0`` (``np.bincount`` parity); its
  status is gap over bridged over valid; its code is the last gap row's
  code, else the last bridged row's code, else 0; ``out_first`` is its first
  row. Keys whose rows are neither valid, bridged nor gap are dropped.
  Output buffers need ``n`` entries; ``scratch`` needs ``4 * n +
  RADIX_BUCKETS``. Returns the sample count.
  """
  m, where = _sort_masked_rows(n, keys, 0, Ptr[u8](), scratch)
  half = scratch + 2 * n * where
  sk = Ptr[u64](half.as_byte())
  sv = half + n
  g = 0
  j = 0
  while j < m:
    cur = sk[j]
    first = sv[j]
    any_gap = False
    any_bridge = False
    any_valid = False
    total = 0.0
    gap_code = 0
    bridge_code = 0
    while j < m and sk[j] == cur:
      r = sv[j]
      is_valid = valid[r] != u8(0)
      is_bridged = bridged[r] != u8(0)
      if gap_row[r] != u8(0):
        any_gap = True
        gap_code = code[r]
      if is_bridged:
        any_bridge = True
        bridge_code = code[r]
      if is_valid:
        any_valid = True
      total += eff[r] if (is_valid or is_bridged) else 0.0
      j += 1
    if any_gap or any_bridge or any_valid:
      out_first[g] = first
      out_sum[g] = total
      if any_gap:
        out_status[g] = SAMPLE_GAP
        out_code[g] = gap_code
      else:
        out_status[g] = SAMPLE_BRIDGED if any_bridge else SAMPLE_VALID
        out_code[g] = bridge_code
      g += 1
  return g


def _is_anchor(means: Ptr[float], gaps: Ptr[u8], i: int) -> bool:
  v = means[i]
  return gaps[i] == u8(0) and v == v and v != POS_INF and v != -POS_INF


def _fill_runs(
  n: int,
  means: Ptr[float],
  gaps: Ptr[u8],
  pos: Ptr[float],
  max_gap_buckets: int,
  apply: bool,
) -> int:
  """Fillable interior gap buckets; interpolates them when ``apply``."""
  total = 0
  i = 0
  while i < n:
    if gaps[i] == u8(0):
      i += 1
      continue
    j = i
    while j < n and gaps[j] != u8(0):
      j += 1
    if (
      i > 0
      and j < n
      and _is_anchor(means, gaps, i - 1)
      and _is_anchor(means, gaps, j)
      and j - i <= max_gap_buckets
    ):
      total += j - i
      if apply:
        x0 = pos[i - 1]
        x1 = pos[j]
        y0 = means[i - 1]
        y1 = means[j]
        for k in range(i, j):
          means[k] = y0 + (y1 - y0) * (pos[k] - x0) / (x1 - x0)
    i = j
  return total


@export
def fill_interior_gaps_c(
  n: int,
  means: Ptr[float],
  gaps: Ptr[u8],
  pos: Ptr[float],
  max_gap_buckets: int,
  max_frac: float,
) -> int:
  """Linearly interpolate interior gap-bucket runs of one series in place.

  A run of ``gaps != 0`` buckets is fillable when a finite non-gap bucket
  bounds it on both sides and it spans at most ``max_gap_buckets``. When
  the fillable buckets exceed ``max_frac * n`` nothing is written. Returns
  the interpolated bucket count (0 when nothing was filled).
  """
  total = _fill_runs(n, means, gaps, pos, max_gap_buckets, False)
  if total == 0 or float(total) > max_frac * float(n):
    return 0
  _fill_runs(n, means, gaps, pos, max_gap_buckets, True)
  return total


@export
def sentinel_fold_c(
  n: int,
  host: Ptr[int],
  bucket: Ptr[int],
  values: Ptr[float],
  status: Ptr[int],
  max_gap_buckets: int,
  max_frac: float,
  scratch: Ptr[int],
  perm: Ptr[int],
  out_hi: Ptr[int],
  out_mean: Ptr[float],
  out_gap: Ptr[u8],
  out_pos: Ptr[float],
) -> int:
  """Per-host 5-minute bucket fold of per-sample sentinel states.

  Rows are grouped by host, then bucket (stable, so each bucket adds its
  covered values in row order from ``0.0``, ``np.bincount`` parity). A
  bucket's mean is its covered sum over its covered count, NaN with gap
  flag 1 when no sample is covered. A host with more than one bucket drops
  its first; its remaining buckets go through ``fill_interior_gaps_c`` at
  their bucket positions. Per host ``h`` (hosts ascending), ``out_hi[k * n
  + h]`` holds, for ``k`` 0..6: host id, valid / bridged / gap sample
  counts, first bucket offset, bucket count, filled bucket count. Bucket
  outputs are packed by host. ``scratch`` needs ``4 * n + RADIX_BUCKETS``,
  ``perm`` ``2 * n``, every other output ``n``. Returns the host count.
  """
  m, where = _sort_masked_rows(n, bucket, 0, Ptr[u8](), scratch)
  half = scratch + 2 * n * where
  for i in range(m):
    perm[i] = half[n + i]
    perm[n + i] = host[perm[i]]
  m, where = _sort_masked_rows(n, perm + n, 0, Ptr[u8](), scratch)
  sv = scratch + 2 * n * where + n
  nan = float("nan")
  nh = 0
  nb = 0
  j = 0
  while j < m:
    h = host[perm[sv[j]]]
    bstart = nb
    n_valid = 0
    n_bridged = 0
    n_gap = 0
    while j < m and host[perm[sv[j]]] == h:
      b = bucket[perm[sv[j]]]
      cnt = 0
      total = 0.0
      while j < m:
        r = perm[sv[j]]
        if host[r] != h or bucket[r] != b:
          break
        st = status[r]
        covered = st != SAMPLE_GAP
        if covered:
          cnt += 1
        total += values[r] if covered else 0.0
        if st == SAMPLE_VALID:
          n_valid += 1
        elif st == SAMPLE_BRIDGED:
          n_bridged += 1
        elif st == SAMPLE_GAP:
          n_gap += 1
        j += 1
      out_pos[nb] = float(b)
      out_mean[nb] = total / float(cnt) if cnt > 0 else nan
      out_gap[nb] = u8(1) if cnt == 0 else u8(0)
      nb += 1
    if nb - bstart > 1:
      for k in range(bstart, nb - 1):
        out_pos[k] = out_pos[k + 1]
        out_mean[k] = out_mean[k + 1]
        out_gap[k] = out_gap[k + 1]
      nb -= 1
    filled = fill_interior_gaps_c(
      nb - bstart,
      out_mean + bstart,
      out_gap + bstart,
      out_pos + bstart,
      max_gap_buckets,
      max_frac,
    )
    out_hi[nh] = h
    out_hi[n + nh] = n_valid
    out_hi[2 * n + nh] = n_bridged
    out_hi[3 * n + nh] = n_gap
    out_hi[4 * n + nh] = bstart
    out_hi[5 * n + nh] = nb - bstart
    out_hi[6 * n + nh] = filled
    nh += 1
  return nh
