/*
 * Multi-phase Python module for Codon feed_collapse under free-threaded 3.14t.
 *
 * Codon 0.20.4 --pyext PyInit/Create2 and from_python converters are GIL-ABI
 * and SIGSEGV / fail under /opt/python3.14t. We build the Codon unit with
 * --lib (@export C entry), marshal List[str] -> owned char** here, and expose
 * METH_VARARGS methods that call Codon C ABI.
 *
 * Declares Py_MOD_GIL_NOT_USED. Concurrent callers must use distinct slot
 * banks (optional slot arg; default 0) under bridge per-slot locks.
 *
 * ``parse_stats_lines_codon`` runs the full feed->delta->collapse->arc
 * pipeline and returns a dict ``{"stats", "procs", "marks", "carry_raw",
 * "carry_arc", "finished": True}`` where ``stats``/``procs`` are columnar
 * SoA dicts (parallel lists) and ``marks`` is a list of bound mark dicts.
 * Optional ``carry_raw``/``carry_arc`` dict kwargs seed the slot's
 * cross-flush counter/arc-rate carry before the run; the Codon slot carry
 * is always cleared first so stale state from a previous caller never
 * leaks across files.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <datetime.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define MAX_CODON_SLOTS 128

extern void feed_and_collapse_fingerprint_c(
  long slot,
  long n,
  char **ptr,
  long *out_raw,
  long *out_collapsed,
  double *out_value_sum,
  double *out_proc_rows
);

extern void parse_stats_lines_codon_run_c(
  long slot,
  long n,
  char **ptr,
  long *out_ns,
  long *out_np
);

extern long parse_stats_file_codon_open_c(
  long slot,
  char *path,
  long flush_max_bytes,
  long resume_byte
);

extern void parse_stats_file_codon_next_c(
  long slot,
  long *out_has_frame,
  long *out_eof,
  long *out_ns,
  long *out_np
);

extern void codon_carry_clear_c(long slot);
extern void codon_carry_raw_set_c(
  long slot,
  char **key_ptr,
  double value,
  double wid,
  double mult,
  double time_val
);
extern void codon_carry_arc_set_c(long slot, char **key_ptr, double value);
/* Codon ``-> str`` exports return ``{ptr, len}`` in rax/rdx. The bytes are
 * not NUL-terminated; strlen walks into the allocator tail and races. */
typedef struct codon_str {
  const char *ptr;
  long len;
} codon_str;

extern long codon_carry_raw_out_count_c(long slot);
extern codon_str codon_carry_raw_out_key_c(long slot, long i);
extern double codon_carry_raw_out_value_c(long slot, long i, long idx);
extern long codon_carry_arc_out_count_c(long slot);
extern codon_str codon_carry_arc_out_key_c(long slot, long i);
extern double codon_carry_arc_out_value_c(long slot, long i);

extern double pss_time_c(long slot, long i);
extern double pss_value_c(long slot, long i);
extern double pss_delta_c(long slot, long i);
extern double pss_arc_c(long slot, long i);
extern long pss_time_null_c(long slot, long i);
extern long pss_value_null_c(long slot, long i);
extern long pss_delta_null_c(long slot, long i);
extern long pss_arc_null_c(long slot, long i);
extern long pss_jid_null_c(long slot, long i);
extern codon_str pss_host_c(long slot, long i);
extern codon_str pss_jid_c(long slot, long i);
extern codon_str pss_type_c(long slot, long i);
extern codon_str pss_dev_c(long slot, long i);
extern codon_str pss_event_c(long slot, long i);
extern codon_str pss_unit_c(long slot, long i);
extern codon_str psp_jid_c(long slot, long i);
extern codon_str psp_host_c(long slot, long i);
extern codon_str psp_proc_c(long slot, long i);
extern codon_str psp_device_c(long slot, long i);
extern long psp_field_c(long slot, long i, long key_idx);
extern long pss_export_f64_c(
  long slot, long col, long n, double *values, long *nulls
);
extern codon_str pss_pack_str_col_c(
  long slot, long col, long n, long *lens, long *nulls
);
extern codon_str psp_pack_str_col_c(
  long slot, long col, long n, long *lens, long *nulls
);
extern long psp_export_fields_c(long slot, long n, long *out);
extern long psm_count_c(long slot);
extern codon_str psm_field_c(long slot, long i, long field_idx);

/* Must match feed_collapse.HOST_PROC_KEYS order. */
static const char *const HOST_PROC_KEYS[] = {
  "uid", "vm_peak", "vm_size", "vm_lck", "vm_hwm", "vm_rss", "vm_data",
  "vm_stk", "vm_exe", "vm_lib", "vm_pte", "vm_swap", "threads",
};
static const Py_ssize_t HOST_PROC_N =
  (Py_ssize_t)(sizeof(HOST_PROC_KEYS) / sizeof(HOST_PROC_KEYS[0]));

static void
free_char_ptrs(char **ptrs, Py_ssize_t n)
{
  if (ptrs == NULL) {
    return;
  }
  for (Py_ssize_t i = 0; i < n; i++) {
    PyMem_Free(ptrs[i]);
  }
  PyMem_Free(ptrs);
}

static int
fill_char_ptrs(PyObject *list_obj, char ***out_ptrs, Py_ssize_t *out_n)
{
  const Py_ssize_t n = PyList_GET_SIZE(list_obj);
  char **ptrs = NULL;
  *out_ptrs = NULL;
  *out_n = n;
  if (n > 0) {
    ptrs = (char **)PyMem_Calloc((size_t)n, sizeof(char *));
    if (ptrs == NULL) {
      PyErr_NoMemory();
      return -1;
    }
  }
  for (Py_ssize_t i = 0; i < n; i++) {
    PyObject *item = PyList_GET_ITEM(list_obj, i);
    if (!PyUnicode_Check(item)) {
      free_char_ptrs(ptrs, i);
      PyErr_SetString(PyExc_TypeError, "lines must be str");
      return -1;
    }
    const char *utf8 = PyUnicode_AsUTF8(item);
    if (utf8 == NULL) {
      free_char_ptrs(ptrs, i);
      return -1;
    }
    const size_t len = strlen(utf8);
    char *owned = (char *)PyMem_Malloc(len + 1);
    if (owned == NULL) {
      free_char_ptrs(ptrs, i);
      PyErr_NoMemory();
      return -1;
    }
    memcpy(owned, utf8, len + 1);
    ptrs[i] = owned;
  }
  *out_ptrs = ptrs;
  return 0;
}

/* Owns a single UTF-8 copy for one Codon-boundary string argument (carry
 * keys). Codon must never see a borrowed PyUnicode_AsUTF8 pointer. */
static int
owned_copy_from_py_str(PyObject *str_obj, char **out)
{
  if (!PyUnicode_Check(str_obj)) {
    PyErr_SetString(PyExc_TypeError, "expected str");
    return -1;
  }
  const char *utf8 = PyUnicode_AsUTF8(str_obj);
  if (utf8 == NULL) {
    return -1;
  }
  const size_t len = strlen(utf8);
  char *owned = (char *)PyMem_Malloc(len + 1);
  if (owned == NULL) {
    PyErr_NoMemory();
    return -1;
  }
  memcpy(owned, utf8, len + 1);
  *out = owned;
  return 0;
}

static int
parse_lines_and_slot(PyObject *args, PyObject **list_obj, long *slot)
{
  *slot = 0;
  if (!PyArg_ParseTuple(args, "O!|l", &PyList_Type, list_obj, slot)) {
    return -1;
  }
  if (*slot < 0 || *slot >= MAX_CODON_SLOTS) {
    PyErr_SetString(PyExc_ValueError, "codon slot out of range");
    return -1;
  }
  return 0;
}

static PyObject *
feed_and_collapse_fingerprint_py(PyObject *self, PyObject *args)
{
  PyObject *list_obj = NULL;
  long slot = 0;
  if (parse_lines_and_slot(args, &list_obj, &slot) < 0) {
    return NULL;
  }
  char **ptrs = NULL;
  Py_ssize_t n = 0;
  if (fill_char_ptrs(list_obj, &ptrs, &n) < 0) {
    return NULL;
  }
  long raw_stats = 0;
  long collapsed_rows = 0;
  double value_sum = 0.0;
  double proc_rows = 0.0;
  feed_and_collapse_fingerprint_c(
    slot,
    (long)n,
    ptrs,
    &raw_stats,
    &collapsed_rows,
    &value_sum,
    &proc_rows
  );
  free_char_ptrs(ptrs, n);
  return Py_BuildValue("(lldd)", raw_stats, collapsed_rows, value_sum, proc_rows);
}

static PyObject *
py_str_from_codon(codon_str s)
{
  if (s.ptr == NULL || s.len <= 0) {
    return PyUnicode_FromString("");
  }
  return PyUnicode_DecodeUTF8(s.ptr, (Py_ssize_t)s.len, NULL);
}

static PyObject *
py_opt_float(long is_null, double value)
{
  if (is_null) {
    return Py_NewRef(Py_None);
  }
  return PyFloat_FromDouble(value);
}

static PyObject *
py_opt_time(long is_null, double unix_s)
{
  PyObject *args = NULL;
  PyObject *dt = NULL;
  if (is_null) {
    return Py_NewRef(Py_None);
  }
  args = Py_BuildValue("(d)", unix_s);
  if (args == NULL) {
    return NULL;
  }
  dt = PyDateTime_FromTimestamp(args);
  Py_DECREF(args);
  return dt;
}

/* carry_raw: {key: [value, wid, mult, time]}. Pushes each entry into the
 * Codon slot via one-element owned key arrays (``codon_carry_raw_set_c``). */
static int
load_carry_raw_dict(long slot, PyObject *carry_raw)
{
  if (carry_raw == NULL || carry_raw == Py_None) {
    return 0;
  }
  if (!PyDict_Check(carry_raw)) {
    PyErr_SetString(PyExc_TypeError, "carry_raw must be a dict");
    return -1;
  }
  PyObject *key;
  PyObject *value;
  Py_ssize_t pos = 0;
  while (PyDict_Next(carry_raw, &pos, &key, &value)) {
    if (!PySequence_Check(value) || PySequence_Size(value) != 4) {
      PyErr_SetString(
        PyExc_TypeError, "carry_raw values must be [value, wid, mult, time]"
      );
      return -1;
    }
    double vals[4];
    for (Py_ssize_t k = 0; k < 4; k++) {
      PyObject *item = PySequence_GetItem(value, k);
      if (item == NULL) {
        return -1;
      }
      vals[k] = PyFloat_AsDouble(item);
      Py_DECREF(item);
      if (PyErr_Occurred()) {
        return -1;
      }
    }
    char *key_copy = NULL;
    if (owned_copy_from_py_str(key, &key_copy) < 0) {
      return -1;
    }
    char *key_arr[1];
    key_arr[0] = key_copy;
    codon_carry_raw_set_c(slot, key_arr, vals[0], vals[1], vals[2], vals[3]);
    PyMem_Free(key_copy);
  }
  return 0;
}

/* carry_arc: {key: float}. */
static int
load_carry_arc_dict(long slot, PyObject *carry_arc)
{
  if (carry_arc == NULL || carry_arc == Py_None) {
    return 0;
  }
  if (!PyDict_Check(carry_arc)) {
    PyErr_SetString(PyExc_TypeError, "carry_arc must be a dict");
    return -1;
  }
  PyObject *key;
  PyObject *value;
  Py_ssize_t pos = 0;
  while (PyDict_Next(carry_arc, &pos, &key, &value)) {
    const double v = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) {
      return -1;
    }
    char *key_copy = NULL;
    if (owned_copy_from_py_str(key, &key_copy) < 0) {
      return -1;
    }
    char *key_arr[1];
    key_arr[0] = key_copy;
    codon_carry_arc_set_c(slot, key_arr, v);
    PyMem_Free(key_copy);
  }
  return 0;
}

static PyObject *
build_carry_raw_dict(long slot)
{
  const long count = codon_carry_raw_out_count_c(slot);
  PyObject *dict = PyDict_New();
  if (dict == NULL) {
    return NULL;
  }
  for (long i = 0; i < count; i++) {
    codon_str key_c = codon_carry_raw_out_key_c(slot, i);
    PyObject *key = py_str_from_codon(key_c);
    if (key == NULL) {
      Py_DECREF(dict);
      return NULL;
    }
    PyObject *vals = PyList_New(4);
    if (vals == NULL) {
      Py_DECREF(key);
      Py_DECREF(dict);
      return NULL;
    }
    int failed = 0;
    for (Py_ssize_t idx = 0; idx < 4; idx++) {
      PyObject *v = PyFloat_FromDouble(
        codon_carry_raw_out_value_c(slot, i, (long)idx)
      );
      if (v == NULL) {
        failed = 1;
        break;
      }
      PyList_SET_ITEM(vals, idx, v);
    }
    if (failed || PyDict_SetItem(dict, key, vals) < 0) {
      Py_DECREF(key);
      Py_DECREF(vals);
      Py_DECREF(dict);
      return NULL;
    }
    Py_DECREF(key);
    Py_DECREF(vals);
  }
  return dict;
}

static PyObject *
build_carry_arc_dict(long slot)
{
  const long count = codon_carry_arc_out_count_c(slot);
  PyObject *dict = PyDict_New();
  if (dict == NULL) {
    return NULL;
  }
  for (long i = 0; i < count; i++) {
    codon_str key_c = codon_carry_arc_out_key_c(slot, i);
    PyObject *key = py_str_from_codon(key_c);
    if (key == NULL) {
      Py_DECREF(dict);
      return NULL;
    }
    PyObject *v = PyFloat_FromDouble(codon_carry_arc_out_value_c(slot, i));
    if (v == NULL || PyDict_SetItem(dict, key, v) < 0) {
      Py_XDECREF(v);
      Py_DECREF(key);
      Py_DECREF(dict);
      return NULL;
    }
    Py_DECREF(key);
    Py_DECREF(v);
  }
  return dict;
}

typedef struct {
  char *data;
  size_t len;
  size_t cap;
} CopyBuf;

static void
copy_buf_init(CopyBuf *buf)
{
  buf->data = NULL;
  buf->len = 0;
  buf->cap = 0;
}

static void
copy_buf_free(CopyBuf *buf)
{
  PyMem_Free(buf->data);
  buf->data = NULL;
  buf->len = 0;
  buf->cap = 0;
}

static int
copy_buf_reserve(CopyBuf *buf, size_t extra)
{
  size_t need = buf->len + extra + 1;
  size_t cap;
  char *next;
  if (need <= buf->cap) {
    return 0;
  }
  cap = buf->cap ? buf->cap : 4096;
  while (cap < need) {
    if (cap > (SIZE_MAX / 2)) {
      return -1;
    }
    cap *= 2;
  }
  next = (char *)PyMem_Realloc(buf->data, cap);
  if (next == NULL) {
    return -1;
  }
  buf->data = next;
  buf->cap = cap;
  return 0;
}

static int
copy_buf_raw(CopyBuf *buf, const char *text, size_t n)
{
  if (copy_buf_reserve(buf, n) < 0) {
    return -1;
  }
  if (n > 0) {
    memcpy(buf->data + buf->len, text, n);
  }
  buf->len += n;
  buf->data[buf->len] = '\0';
  return 0;
}

static int
copy_buf_escape(CopyBuf *buf, const char *text, size_t n)
{
  size_t i;
  for (i = 0; i < n; i++) {
    char ch = text[i];
    const char *esc = NULL;
    size_t esc_n = 0;
    if (ch == '\\') {
      esc = "\\\\";
      esc_n = 2;
    } else if (ch == '\t') {
      esc = "\\t";
      esc_n = 2;
    } else if (ch == '\n') {
      esc = "\\n";
      esc_n = 2;
    } else if (ch == '\r') {
      esc = "\\r";
      esc_n = 2;
    }
    if (esc != NULL) {
      if (copy_buf_raw(buf, esc, esc_n) < 0) {
        return -1;
      }
    } else if (copy_buf_raw(buf, &ch, 1) < 0) {
      return -1;
    }
  }
  return 0;
}

static int
copy_buf_codon(CopyBuf *buf, codon_str value)
{
  if (value.ptr == NULL || value.len <= 0) {
    return 0;
  }
  return copy_buf_escape(buf, value.ptr, (size_t)value.len);
}

static int
copy_buf_null(CopyBuf *buf)
{
  return copy_buf_raw(buf, "\\N", 2);
}

static int
copy_buf_float(CopyBuf *buf, double value)
{
  char *text = PyOS_double_to_string(value, 'r', 0, Py_DTSF_ADD_DOT_0, NULL);
  int rc;
  if (text == NULL) {
    return -1;
  }
  rc = copy_buf_escape(buf, text, strlen(text));
  PyMem_Free(text);
  return rc;
}

static int
copy_buf_time(CopyBuf *buf, double unix_s)
{
  double whole = 0.0;
  double frac = modf(unix_s, &whole);
  long usec;
  time_t sec;
  struct tm tm_buf;
  char text[64];
  int n;
  if (frac < 0.0) {
    frac += 1.0;
    whole -= 1.0;
  }
  usec = (long)llround(frac * 1000000.0);
  if (usec >= 1000000L) {
    usec -= 1000000L;
    whole += 1.0;
  }
  if (usec < 0) {
    usec = 0;
  }
  sec = (time_t)whole;
  if (gmtime_r(&sec, &tm_buf) == NULL) {
    return -1;
  }
  if (usec == 0) {
    n = snprintf(
      text,
      sizeof(text),
      "%04d-%02d-%02dT%02d:%02d:%02d+00:00",
      tm_buf.tm_year + 1900,
      tm_buf.tm_mon + 1,
      tm_buf.tm_mday,
      tm_buf.tm_hour,
      tm_buf.tm_min,
      tm_buf.tm_sec
    );
  } else {
    n = snprintf(
      text,
      sizeof(text),
      "%04d-%02d-%02dT%02d:%02d:%02d.%06ld+00:00",
      tm_buf.tm_year + 1900,
      tm_buf.tm_mon + 1,
      tm_buf.tm_mday,
      tm_buf.tm_hour,
      tm_buf.tm_min,
      tm_buf.tm_sec,
      usec
    );
  }
  if (n < 0 || (size_t)n >= sizeof(text)) {
    return -1;
  }
  return copy_buf_raw(buf, text, (size_t)n);
}

static int
copy_buf_long(CopyBuf *buf, long value)
{
  char text[32];
  int n = snprintf(text, sizeof(text), "%ld", value);
  if (n < 0 || (size_t)n >= sizeof(text)) {
    return -1;
  }
  return copy_buf_raw(buf, text, (size_t)n);
}

static int
append_host_copy_row_local(
  CopyBuf *buf,
  int time_null,
  double time_v,
  const char *host,
  long host_len,
  int jid_null,
  const char *jid,
  long jid_len,
  const char *typ,
  long typ_len,
  const char *dev,
  long dev_len,
  const char *event,
  long event_len,
  const char *unit,
  long unit_len,
  int value_null,
  double value_v,
  int delta_null,
  double delta_v,
  int arc_null,
  double arc_v
)
{
  int rc = 0;
  if (time_null) {
    rc |= copy_buf_null(buf);
  } else {
    rc |= copy_buf_time(buf, time_v);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (host_len > 0) {
    rc |= copy_buf_escape(buf, host, (size_t)host_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (jid_null) {
    rc |= copy_buf_null(buf);
  } else if (jid_len > 0) {
    rc |= copy_buf_escape(buf, jid, (size_t)jid_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (typ_len > 0) {
    rc |= copy_buf_escape(buf, typ, (size_t)typ_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (dev_len > 0) {
    rc |= copy_buf_escape(buf, dev, (size_t)dev_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (event_len > 0) {
    rc |= copy_buf_escape(buf, event, (size_t)event_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (unit_len > 0) {
    rc |= copy_buf_escape(buf, unit, (size_t)unit_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (value_null) {
    rc |= copy_buf_null(buf);
  } else {
    rc |= copy_buf_float(buf, value_v);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (delta_null) {
    rc |= copy_buf_null(buf);
  } else {
    rc |= copy_buf_float(buf, delta_v);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (arc_null) {
    rc |= copy_buf_null(buf);
  } else {
    rc |= copy_buf_float(buf, arc_v);
  }
  rc |= copy_buf_raw(buf, "\n", 1);
  return rc;
}

static int
append_proc_copy_row_local(
  CopyBuf *buf,
  const char *jid,
  long jid_len,
  const char *host,
  long host_len,
  const char *proc,
  long proc_len,
  const char *device,
  long device_len,
  const long *fields
)
{
  Py_ssize_t k;
  int rc = 0;
  if (jid_len > 0) {
    rc |= copy_buf_escape(buf, jid, (size_t)jid_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (host_len > 0) {
    rc |= copy_buf_escape(buf, host, (size_t)host_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (proc_len > 0) {
    rc |= copy_buf_escape(buf, proc, (size_t)proc_len);
  }
  rc |= copy_buf_raw(buf, "\t", 1);
  if (device_len > 0) {
    rc |= copy_buf_escape(buf, device, (size_t)device_len);
  }
  for (k = 0; k < HOST_PROC_N; k++) {
    long field = fields[(size_t)k];
    rc |= copy_buf_raw(buf, "\t", 1);
    if (field < 0) {
      rc |= copy_buf_null(buf);
    } else {
      rc |= copy_buf_long(buf, field);
    }
  }
  rc |= copy_buf_raw(buf, "\n", 1);
  return rc;
}

typedef codon_str (*hps_pack_fn)(
  long slot, long col, long n, long *lens, long *nulls
);

typedef struct PackedStr {
  char *bytes;
  long *lens;
  long *nulls;
  const char *cursor;
} PackedStr;

typedef struct F64Col {
  double *values;
  long *nulls;
} F64Col;

static void
packed_str_clear(PackedStr *col)
{
  PyMem_Free(col->bytes);
  PyMem_Free(col->lens);
  PyMem_Free(col->nulls);
  col->bytes = NULL;
  col->lens = NULL;
  col->nulls = NULL;
  col->cursor = NULL;
}

static void
f64_clear(F64Col *col)
{
  PyMem_Free(col->values);
  PyMem_Free(col->nulls);
  col->values = NULL;
  col->nulls = NULL;
}

static int
packed_str_load(
  PackedStr *col, hps_pack_fn pack, long slot, long which, long n
)
{
  codon_str packed;
  memset(col, 0, sizeof(*col));
  if (n <= 0) {
    return 0;
  }
  col->lens = PyMem_Calloc((size_t)n, sizeof(long));
  col->nulls = PyMem_Calloc((size_t)n, sizeof(long));
  if (col->lens == NULL || col->nulls == NULL) {
    return -1;
  }
  packed = pack(slot, which, n, col->lens, col->nulls);
  if (packed.len > 0) {
    if (packed.ptr == NULL) {
      return -1;
    }
    col->bytes = PyMem_Malloc((size_t)packed.len);
    if (col->bytes == NULL) {
      return -1;
    }
    memcpy(col->bytes, packed.ptr, (size_t)packed.len);
    col->cursor = col->bytes;
  }
  return 0;
}

static void
packed_take(
  PackedStr *col, long i, const char **ptr, long *len, int *is_null
)
{
  *is_null = (col->nulls != NULL && col->nulls[i] != 0);
  *len = (col->lens != NULL) ? col->lens[i] : 0;
  *ptr = col->cursor;
  if (col->cursor != NULL && *len > 0) {
    col->cursor += *len;
  }
}

static int
f64_load(F64Col *col, long slot, long which, long n)
{
  memset(col, 0, sizeof(*col));
  if (n <= 0) {
    return 0;
  }
  col->values = PyMem_Calloc((size_t)n, sizeof(double));
  col->nulls = PyMem_Calloc((size_t)n, sizeof(long));
  if (col->values == NULL || col->nulls == NULL) {
    return -1;
  }
  pss_export_f64_c(slot, which, n, col->values, col->nulls);
  return 0;
}

static PyObject *
build_copy_bytes_dict(long slot, long n_stats, long n_procs)
{
  CopyBuf host_buf;
  CopyBuf proc_buf;
  PyObject *stats = NULL;
  PyObject *host_bytes = NULL;
  PyObject *proc_bytes = NULL;
  F64Col time_c, value_c, delta_c, arc_c;
  PackedStr host_s, jid_s, type_s, dev_s, event_s, unit_s;
  PackedStr proc_jid_s, proc_host_s, proc_name_s, proc_dev_s;
  long *proc_fields = NULL;
  long i;
  memset(&time_c, 0, sizeof(time_c));
  memset(&value_c, 0, sizeof(value_c));
  memset(&delta_c, 0, sizeof(delta_c));
  memset(&arc_c, 0, sizeof(arc_c));
  memset(&host_s, 0, sizeof(host_s));
  memset(&jid_s, 0, sizeof(jid_s));
  memset(&type_s, 0, sizeof(type_s));
  memset(&dev_s, 0, sizeof(dev_s));
  memset(&event_s, 0, sizeof(event_s));
  memset(&unit_s, 0, sizeof(unit_s));
  memset(&proc_jid_s, 0, sizeof(proc_jid_s));
  memset(&proc_host_s, 0, sizeof(proc_host_s));
  memset(&proc_name_s, 0, sizeof(proc_name_s));
  memset(&proc_dev_s, 0, sizeof(proc_dev_s));
  copy_buf_init(&host_buf);
  copy_buf_init(&proc_buf);
  if (
    f64_load(&time_c, slot, 0, n_stats) < 0
    || f64_load(&value_c, slot, 1, n_stats) < 0
    || f64_load(&delta_c, slot, 2, n_stats) < 0
    || f64_load(&arc_c, slot, 3, n_stats) < 0
    || packed_str_load(&host_s, pss_pack_str_col_c, slot, 0, n_stats) < 0
    || packed_str_load(&jid_s, pss_pack_str_col_c, slot, 1, n_stats) < 0
    || packed_str_load(&type_s, pss_pack_str_col_c, slot, 2, n_stats) < 0
    || packed_str_load(&dev_s, pss_pack_str_col_c, slot, 3, n_stats) < 0
    || packed_str_load(&event_s, pss_pack_str_col_c, slot, 4, n_stats) < 0
    || packed_str_load(&unit_s, pss_pack_str_col_c, slot, 5, n_stats) < 0
  ) {
    goto fail;
  }
  if (n_procs > 0) {
    proc_fields = PyMem_Calloc(
      (size_t)n_procs * (size_t)HOST_PROC_N, sizeof(long)
    );
    if (proc_fields == NULL) {
      goto fail;
    }
    psp_export_fields_c(slot, n_procs, proc_fields);
  }
  if (
    packed_str_load(&proc_jid_s, psp_pack_str_col_c, slot, 0, n_procs) < 0
    || packed_str_load(&proc_host_s, psp_pack_str_col_c, slot, 1, n_procs) < 0
    || packed_str_load(&proc_name_s, psp_pack_str_col_c, slot, 2, n_procs) < 0
    || packed_str_load(&proc_dev_s, psp_pack_str_col_c, slot, 3, n_procs) < 0
  ) {
    goto fail;
  }
  for (i = 0; i < n_stats; i++) {
    const char *host_p;
    const char *jid_p;
    const char *type_p;
    const char *dev_p;
    const char *event_p;
    const char *unit_p;
    long host_n;
    long jid_n;
    long type_n;
    long dev_n;
    long event_n;
    long unit_n;
    int host_null;
    int jid_null;
    int type_null;
    int dev_null;
    int event_null;
    int unit_null;
    packed_take(&host_s, i, &host_p, &host_n, &host_null);
    packed_take(&jid_s, i, &jid_p, &jid_n, &jid_null);
    packed_take(&type_s, i, &type_p, &type_n, &type_null);
    packed_take(&dev_s, i, &dev_p, &dev_n, &dev_null);
    packed_take(&event_s, i, &event_p, &event_n, &event_null);
    packed_take(&unit_s, i, &unit_p, &unit_n, &unit_null);
    if (append_host_copy_row_local(
      &host_buf,
      time_c.nulls[i] != 0,
      time_c.values[i],
      host_p,
      host_n,
      jid_null,
      jid_p,
      jid_n,
      type_p,
      type_n,
      dev_p,
      dev_n,
      event_p,
      event_n,
      unit_p,
      unit_n,
      value_c.nulls[i] != 0,
      value_c.values[i],
      delta_c.nulls[i] != 0,
      delta_c.values[i],
      arc_c.nulls[i] != 0,
      arc_c.values[i]
    ) < 0) {
      goto fail;
    }
  }
  for (i = 0; i < n_procs; i++) {
    const char *jid_p;
    const char *host_p;
    const char *proc_p;
    const char *dev_p;
    long jid_n;
    long host_n;
    long proc_n;
    long dev_n;
    int ignored;
    packed_take(&proc_jid_s, i, &jid_p, &jid_n, &ignored);
    packed_take(&proc_host_s, i, &host_p, &host_n, &ignored);
    packed_take(&proc_name_s, i, &proc_p, &proc_n, &ignored);
    packed_take(&proc_dev_s, i, &dev_p, &dev_n, &ignored);
    if (append_proc_copy_row_local(
      &proc_buf,
      jid_p,
      jid_n,
      host_p,
      host_n,
      proc_p,
      proc_n,
      dev_p,
      dev_n,
      proc_fields + (i * (long)HOST_PROC_N)
    ) < 0) {
      goto fail;
    }
  }
  stats = PyDict_New();
  host_bytes = PyBytes_FromStringAndSize(
    host_buf.data == NULL ? "" : host_buf.data,
    (Py_ssize_t)host_buf.len
  );
  proc_bytes = PyBytes_FromStringAndSize(
    proc_buf.data == NULL ? "" : proc_buf.data,
    (Py_ssize_t)proc_buf.len
  );
  if (
    stats == NULL || host_bytes == NULL || proc_bytes == NULL
    || PyDict_SetItemString(stats, "host_copy", host_bytes) < 0
    || PyDict_SetItemString(stats, "proc_copy", proc_bytes) < 0
  ) {
    goto fail;
  }
  Py_DECREF(host_bytes);
  Py_DECREF(proc_bytes);
  copy_buf_free(&host_buf);
  copy_buf_free(&proc_buf);
  f64_clear(&time_c);
  f64_clear(&value_c);
  f64_clear(&delta_c);
  f64_clear(&arc_c);
  packed_str_clear(&host_s);
  packed_str_clear(&jid_s);
  packed_str_clear(&type_s);
  packed_str_clear(&dev_s);
  packed_str_clear(&event_s);
  packed_str_clear(&unit_s);
  packed_str_clear(&proc_jid_s);
  packed_str_clear(&proc_host_s);
  packed_str_clear(&proc_name_s);
  packed_str_clear(&proc_dev_s);
  PyMem_Free(proc_fields);
  return stats;
fail:
  Py_XDECREF(proc_bytes);
  Py_XDECREF(host_bytes);
  Py_XDECREF(stats);
  copy_buf_free(&host_buf);
  copy_buf_free(&proc_buf);
  f64_clear(&time_c);
  f64_clear(&value_c);
  f64_clear(&delta_c);
  f64_clear(&arc_c);
  packed_str_clear(&host_s);
  packed_str_clear(&jid_s);
  packed_str_clear(&type_s);
  packed_str_clear(&dev_s);
  packed_str_clear(&event_s);
  packed_str_clear(&unit_s);
  packed_str_clear(&proc_jid_s);
  packed_str_clear(&proc_host_s);
  packed_str_clear(&proc_name_s);
  packed_str_clear(&proc_dev_s);
  PyMem_Free(proc_fields);
  if (!PyErr_Occurred()) {
    PyErr_NoMemory();
  }
  return NULL;
}

static PyObject *finish_slot_result(long slot, PyObject *stats, PyObject *procs);

static PyObject *
build_finished_parse_dict_from_slot(
  long slot,
  long n_stats,
  long n_procs,
  int emit_insert
)
{
  if (!emit_insert) {
    PyObject *stats = build_copy_bytes_dict(slot, n_stats, n_procs);
    PyObject *procs;
    if (stats == NULL) {
      return NULL;
    }
    procs = PyDict_New();
    if (procs == NULL) {
      Py_DECREF(stats);
      return NULL;
    }
    return finish_slot_result(slot, stats, procs);
  }
  /* Columnar SoA: one Python list per column (no per-row dicts). */
  PyObject *stats = PyDict_New();
  if (stats == NULL) {
    return NULL;
  }
  {
    PyObject *col_time = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_host = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_jid = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_type = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_dev = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_event = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_unit = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_value = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_delta = PyList_New((Py_ssize_t)n_stats);
    PyObject *col_arc = PyList_New((Py_ssize_t)n_stats);
    if (
      col_time == NULL || col_host == NULL || col_jid == NULL
      || col_type == NULL || col_dev == NULL || col_event == NULL
      || col_unit == NULL || col_value == NULL || col_delta == NULL
      || col_arc == NULL
    ) {
      Py_XDECREF(col_time);
      Py_XDECREF(col_host);
      Py_XDECREF(col_jid);
      Py_XDECREF(col_type);
      Py_XDECREF(col_dev);
      Py_XDECREF(col_event);
      Py_XDECREF(col_unit);
      Py_XDECREF(col_value);
      Py_XDECREF(col_delta);
      Py_XDECREF(col_arc);
      Py_DECREF(stats);
      return NULL;
    }
    for (long i = 0; i < n_stats; i++) {
      PyObject *v;
      v = py_opt_time(pss_time_null_c(slot, i), pss_time_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_time, (Py_ssize_t)i, v);
      v = py_str_from_codon(pss_host_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_host, (Py_ssize_t)i, v);
      if (pss_jid_null_c(slot, i)) {
        v = Py_NewRef(Py_None);
      } else {
        v = py_str_from_codon(pss_jid_c(slot, i));
      }
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_jid, (Py_ssize_t)i, v);
      v = py_str_from_codon(pss_type_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_type, (Py_ssize_t)i, v);
      v = py_str_from_codon(pss_dev_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_dev, (Py_ssize_t)i, v);
      v = py_str_from_codon(pss_event_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_event, (Py_ssize_t)i, v);
      v = py_str_from_codon(pss_unit_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_unit, (Py_ssize_t)i, v);
      v = py_opt_float(pss_value_null_c(slot, i), pss_value_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_value, (Py_ssize_t)i, v);
      v = py_opt_float(pss_delta_null_c(slot, i), pss_delta_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_delta, (Py_ssize_t)i, v);
      v = py_opt_float(pss_arc_null_c(slot, i), pss_arc_c(slot, i));
      if (v == NULL) {
        goto stats_fail;
      }
      PyList_SET_ITEM(col_arc, (Py_ssize_t)i, v);
      continue;
    stats_fail:
      Py_DECREF(col_time);
      Py_DECREF(col_host);
      Py_DECREF(col_jid);
      Py_DECREF(col_type);
      Py_DECREF(col_dev);
      Py_DECREF(col_event);
      Py_DECREF(col_unit);
      Py_DECREF(col_value);
      Py_DECREF(col_delta);
      Py_DECREF(col_arc);
      Py_DECREF(stats);
      return NULL;
    }
    int rc_s = 0;
    rc_s |= PyDict_SetItemString(stats, "time", col_time) < 0;
    rc_s |= PyDict_SetItemString(stats, "host", col_host) < 0;
    rc_s |= PyDict_SetItemString(stats, "jid", col_jid) < 0;
    rc_s |= PyDict_SetItemString(stats, "type", col_type) < 0;
    rc_s |= PyDict_SetItemString(stats, "dev", col_dev) < 0;
    rc_s |= PyDict_SetItemString(stats, "event", col_event) < 0;
    rc_s |= PyDict_SetItemString(stats, "unit", col_unit) < 0;
    rc_s |= PyDict_SetItemString(stats, "value", col_value) < 0;
    rc_s |= PyDict_SetItemString(stats, "delta", col_delta) < 0;
    rc_s |= PyDict_SetItemString(stats, "arc", col_arc) < 0;
    Py_DECREF(col_time);
    Py_DECREF(col_host);
    Py_DECREF(col_jid);
    Py_DECREF(col_type);
    Py_DECREF(col_dev);
    Py_DECREF(col_event);
    Py_DECREF(col_unit);
    Py_DECREF(col_value);
    Py_DECREF(col_delta);
    Py_DECREF(col_arc);
    if (rc_s) {
      Py_DECREF(stats);
      return NULL;
    }
  }

  /* Procs: columnar SoA (parallel lists; sparse metrics as None). */
  PyObject *procs = PyDict_New();
  if (procs == NULL) {
    Py_DECREF(stats);
    return NULL;
  }
  PyObject *col_jid = PyList_New((Py_ssize_t)n_procs);
  PyObject *col_host = PyList_New((Py_ssize_t)n_procs);
  PyObject *col_proc = PyList_New((Py_ssize_t)n_procs);
  PyObject *col_device = PyList_New((Py_ssize_t)n_procs);
  PyObject *metric_cols[HOST_PROC_N];
  for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
    metric_cols[k] = NULL;
  }
  for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
    metric_cols[k] = PyList_New((Py_ssize_t)n_procs);
    if (metric_cols[k] == NULL) {
      goto procs_fail;
    }
  }
  if (
    col_jid == NULL || col_host == NULL || col_proc == NULL
    || col_device == NULL
  ) {
    goto procs_fail;
  }
  for (long i = 0; i < n_procs; i++) {
    PyObject *v;
    v = py_str_from_codon(psp_jid_c(slot, i));
    if (v == NULL) {
      goto procs_fail;
    }
    PyList_SET_ITEM(col_jid, (Py_ssize_t)i, v);
    v = py_str_from_codon(psp_host_c(slot, i));
    if (v == NULL) {
      goto procs_fail;
    }
    PyList_SET_ITEM(col_host, (Py_ssize_t)i, v);
    v = py_str_from_codon(psp_proc_c(slot, i));
    if (v == NULL) {
      goto procs_fail;
    }
    PyList_SET_ITEM(col_proc, (Py_ssize_t)i, v);
    v = py_str_from_codon(psp_device_c(slot, i));
    if (v == NULL) {
      goto procs_fail;
    }
    PyList_SET_ITEM(col_device, (Py_ssize_t)i, v);
    for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
      long fv = psp_field_c(slot, i, (long)k);
      if (fv < 0) {
        v = Py_NewRef(Py_None);
      } else {
        v = PyLong_FromLong(fv);
      }
      if (v == NULL) {
        goto procs_fail;
      }
      PyList_SET_ITEM(metric_cols[k], (Py_ssize_t)i, v);
    }
  }
  {
    int rc_p = 0;
    rc_p |= PyDict_SetItemString(procs, "jid", col_jid) < 0;
    rc_p |= PyDict_SetItemString(procs, "host", col_host) < 0;
    rc_p |= PyDict_SetItemString(procs, "proc", col_proc) < 0;
    rc_p |= PyDict_SetItemString(procs, "device", col_device) < 0;
    for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
      rc_p |= PyDict_SetItemString(procs, HOST_PROC_KEYS[k], metric_cols[k]) < 0;
    }
    Py_DECREF(col_jid);
    Py_DECREF(col_host);
    Py_DECREF(col_proc);
    Py_DECREF(col_device);
    for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
      Py_DECREF(metric_cols[k]);
    }
    if (rc_p) {
      Py_DECREF(stats);
      Py_DECREF(procs);
      return NULL;
    }
  }
  goto procs_ok;

procs_fail:
  Py_XDECREF(col_jid);
  Py_XDECREF(col_host);
  Py_XDECREF(col_proc);
  Py_XDECREF(col_device);
  for (Py_ssize_t k = 0; k < HOST_PROC_N; k++) {
    Py_XDECREF(metric_cols[k]);
  }
  Py_DECREF(procs);
  Py_DECREF(stats);
  return NULL;

procs_ok:
  return finish_slot_result(slot, stats, procs);
}

static PyObject *
finish_slot_result(long slot, PyObject *stats, PyObject *procs)
{
  {
    long n_marks = psm_count_c(slot);
    PyObject *marks = PyList_New((Py_ssize_t)n_marks);
    if (marks == NULL) {
      Py_DECREF(stats);
      Py_DECREF(procs);
      return NULL;
    }
    static const char *const MARK_KEYS[] = {
      "kind", "jids", "jid", "cpus", "gpus", "cg", "time", "host",
    };
    for (long i = 0; i < n_marks; i++) {
      PyObject *row = PyDict_New();
      if (row == NULL) {
        Py_DECREF(stats);
        Py_DECREF(procs);
        Py_DECREF(marks);
        return NULL;
      }
      int rc = 0;
      for (long fi = 0; fi < 8; fi++) {
        PyObject *v = py_str_from_codon(psm_field_c(slot, i, fi));
        rc |= v == NULL || PyDict_SetItemString(row, MARK_KEYS[fi], v) < 0;
        Py_XDECREF(v);
      }
      if (rc) {
        Py_DECREF(row);
        Py_DECREF(stats);
        Py_DECREF(procs);
        Py_DECREF(marks);
        return NULL;
      }
      PyList_SET_ITEM(marks, (Py_ssize_t)i, row);
    }

    PyObject *carry_raw_out = build_carry_raw_dict(slot);
    if (carry_raw_out == NULL) {
      Py_DECREF(stats);
      Py_DECREF(procs);
      Py_DECREF(marks);
      return NULL;
    }
    PyObject *carry_arc_out = build_carry_arc_dict(slot);
    if (carry_arc_out == NULL) {
      Py_DECREF(stats);
      Py_DECREF(procs);
      Py_DECREF(marks);
      Py_DECREF(carry_raw_out);
      return NULL;
    }

    PyObject *result = PyDict_New();
    if (result == NULL) {
      Py_DECREF(stats);
      Py_DECREF(procs);
      Py_DECREF(marks);
      Py_DECREF(carry_raw_out);
      Py_DECREF(carry_arc_out);
      return NULL;
    }
    int rc = 0;
    rc |= PyDict_SetItemString(result, "stats", stats) < 0;
    rc |= PyDict_SetItemString(result, "procs", procs) < 0;
    rc |= PyDict_SetItemString(result, "marks", marks) < 0;
    rc |= PyDict_SetItemString(result, "carry_raw", carry_raw_out) < 0;
    rc |= PyDict_SetItemString(result, "carry_arc", carry_arc_out) < 0;
    Py_DECREF(stats);
    Py_DECREF(procs);
    Py_DECREF(marks);
    Py_DECREF(carry_raw_out);
    Py_DECREF(carry_arc_out);
    if (rc) {
      Py_DECREF(result);
      return NULL;
    }
    if (PyDict_SetItemString(result, "finished", Py_True) < 0) {
      Py_DECREF(result);
      return NULL;
    }
    if (PyDict_SetItemString(result, "copy_unique", Py_True) < 0) {
      Py_DECREF(result);
      return NULL;
    }
    return result;
  }
}

static PyObject *
parse_stats_lines_codon_py(PyObject *self, PyObject *args, PyObject *kwargs)
{
  static char *kwlist[] = {"lines", "slot", "carry_raw", "carry_arc", NULL};
  PyObject *list_obj = NULL;
  long slot = 0;
  PyObject *carry_raw = NULL;
  PyObject *carry_arc = NULL;
  if (!PyArg_ParseTupleAndKeywords(
        args, kwargs, "O!|lOO", kwlist,
        &PyList_Type, &list_obj, &slot, &carry_raw, &carry_arc
      )) {
    return NULL;
  }
  if (slot < 0 || slot >= MAX_CODON_SLOTS) {
    PyErr_SetString(PyExc_ValueError, "codon slot out of range");
    return NULL;
  }

  /* Codon slot carry never leaks across unrelated calls; caller supplies
   * the full carry state to seed (empty dict/None for a fresh start). */
  codon_carry_clear_c(slot);
  if (load_carry_raw_dict(slot, carry_raw) < 0) {
    return NULL;
  }
  if (load_carry_arc_dict(slot, carry_arc) < 0) {
    return NULL;
  }

  char **ptrs = NULL;
  Py_ssize_t n = 0;
  if (fill_char_ptrs(list_obj, &ptrs, &n) < 0) {
    return NULL;
  }
  long n_stats = 0;
  long n_procs = 0;
  parse_stats_lines_codon_run_c(slot, (long)n, ptrs, &n_stats, &n_procs);
  free_char_ptrs(ptrs, n);
  return build_finished_parse_dict_from_slot(slot, n_stats, n_procs, 1);
}

static PyObject *
parse_stats_file_codon_open_py(PyObject *self, PyObject *args, PyObject *kwargs)
{
  static char *kwlist[] = {
    "path", "flush_max_bytes", "resume_byte", "slot", NULL
  };
  PyObject *path_obj = NULL;
  long flush_max_bytes = 0;
  long resume_byte = 0;
  long slot = 0;
  if (!PyArg_ParseTupleAndKeywords(
        args, kwargs, "O!|lll", kwlist,
        &PyUnicode_Type, &path_obj, &flush_max_bytes, &resume_byte, &slot
      )) {
    return NULL;
  }
  if (slot < 0 || slot >= MAX_CODON_SLOTS) {
    PyErr_SetString(PyExc_ValueError, "codon slot out of range");
    return NULL;
  }
  char *path_owned = NULL;
  if (owned_copy_from_py_str(path_obj, &path_owned) < 0) {
    return NULL;
  }
  const long ok = parse_stats_file_codon_open_c(
    slot, path_owned, flush_max_bytes, resume_byte
  );
  PyMem_Free(path_owned);
  return PyBool_FromLong(ok);
}

static PyObject *
parse_stats_file_codon_next_py(PyObject *self, PyObject *args, PyObject *kwargs)
{
  static char *kwlist[] = {"slot", "emit_insert", NULL};
  long slot = 0;
  long emit_insert = 0;
  if (!PyArg_ParseTupleAndKeywords(
        args, kwargs, "|ll", kwlist, &slot, &emit_insert
      )) {
    return NULL;
  }
  if (slot < 0 || slot >= MAX_CODON_SLOTS) {
    PyErr_SetString(PyExc_ValueError, "codon slot out of range");
    return NULL;
  }
  long has_frame = 0;
  long eof = 0;
  long n_stats = 0;
  long n_procs = 0;
  parse_stats_file_codon_next_c(
    slot, &has_frame, &eof, &n_stats, &n_procs
  );
  if (!has_frame) {
    return Py_NewRef(Py_None);
  }
  return build_finished_parse_dict_from_slot(
    slot, n_stats, n_procs, emit_insert ? 1 : 0
  );
}


static PyMethodDef codon_methods[] = {
  {
    "feed_and_collapse_fingerprint",
    feed_and_collapse_fingerprint_py,
    METH_VARARGS,
    "Fingerprint: (raw_stats, collapsed_rows, value_sum, proc_rows) [slot optional]",
  },
  {
    "parse_stats_lines_codon",
    (PyCFunction)parse_stats_lines_codon_py,
    METH_VARARGS | METH_KEYWORDS,
    "Full feed->delta->collapse->arc parse. Args: lines, slot=0, "
    "carry_raw=None, carry_arc=None. Returns dict with columnar stats/"
    "procs list/marks/carry_raw/carry_arc/finished=True.",
  },
  {
    "parse_stats_file_codon_open",
    (PyCFunction)parse_stats_file_codon_open_py,
    METH_VARARGS | METH_KEYWORDS,
    "Open stats path in Codon kernel for batched read/parse on slot.",
  },
  {
    "parse_stats_file_codon_next",
    (PyCFunction)parse_stats_file_codon_next_py,
    METH_VARARGS | METH_KEYWORDS,
    "Read/parse next flush window; None when session exhausted.",
  },
  {NULL, NULL, 0, NULL},
};

static int
hpcperfstats_codon_parse_exec(PyObject *module)
{
  if (PyModule_AddFunctions(module, codon_methods) < 0) {
    return -1;
  }
  if (PyModule_AddIntConstant(module, "MAX_CODON_SLOTS", MAX_CODON_SLOTS) < 0) {
    return -1;
  }
  return 0;
}

static PyModuleDef_Slot codon_slots[] = {
  {Py_mod_exec, (void *)hpcperfstats_codon_parse_exec},
  {Py_mod_gil, Py_MOD_GIL_NOT_USED},
  {0, NULL},
};

static struct PyModuleDef codon_module = {
  PyModuleDef_HEAD_INIT,
  .m_name = "hpcperfstats_codon_parse",
  .m_doc = "Codon feed+delta+collapse+arc (C-ABI + multi-phase FT init, GIL_NOT_USED)",
  .m_size = 0,
  .m_slots = codon_slots,
};

PyMODINIT_FUNC
PyInit_hpcperfstats_codon_parse(void)
{
  PyDateTime_IMPORT;
  return PyModuleDef_Init(&codon_module);
}
