/*
 * Multi-phase Python module for the update_metrics Codon kernels under
 * free-threaded 3.14t (``hpcperfstats_codon_metrics``).
 *
 * The Codon unit (``metrics_kernels.py``) is built with ``--lib`` and
 * exposes plain C entry points that read and write caller buffers. This shim
 * borrows those buffers from NumPy arrays through the buffer protocol,
 * checks byte lengths and passes raw pointers through. It keeps no state of
 * its own, so it declares ``Py_MOD_GIL_NOT_USED`` and many metrics-pool
 * threads may call it at once (one job per thread, plan decision D4).
 *
 * dtype discipline lives in the CPython bridge
 * (``hpcperfstats/analysis/metrics/lib/columnar/codon_bridge.py``): every
 * array handed in is C-contiguous with the documented dtype. The shim only
 * refuses buffers that are too short.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

extern long copy_count_rows_c(const uint8_t *buf, long n, long has_owned);
extern long copy_decode_c(
  const uint8_t *buf,
  long n,
  long has_owned,
  long nrows,
  int64_t *out_time,
  int32_t *out_host,
  int32_t *out_type,
  int32_t *out_event,
  int32_t *out_dev,
  uint8_t *out_owned,
  double *out_value,
  double *out_arc,
  double *out_delta,
  long name_cap,
  int64_t *out_name_col,
  int64_t *out_name_off,
  int64_t *out_name_len
);
extern long group_reduce_c(
  long n,
  const int64_t *keys,
  const double *values,
  long has_mask,
  const uint8_t *mask,
  int64_t *scratch,
  int64_t *out_keys,
  int64_t *out_cnt,
  double *out_sum,
  double *out_max
);
#define RADIX_BUCKETS 65536
extern long grid_mean_c(
  long n,
  const int64_t *cells,
  const double *values,
  long size,
  int64_t *cnt_scratch,
  double *out_grid
);
extern long join_sorted_c(
  long nl,
  const int64_t *left,
  long nr,
  const int64_t *right,
  int64_t *out_li,
  int64_t *out_ri
);

#define ERR_NAME_CAP (-4)
#define MAX_VIEWS 16

typedef struct views {
  Py_buffer v[MAX_VIEWS];
  int n;
} views;

static void
release_views(views *vs)
{
  for (int i = 0; i < vs->n; i++) {
    PyBuffer_Release(&vs->v[i]);
  }
  vs->n = 0;
}

/* Borrow a contiguous buffer with at least ``need`` bytes. */
static void *
take_buffer(
  views *vs, PyObject *obj, int writable, Py_ssize_t need, const char *what
)
{
  if (vs->n >= MAX_VIEWS) {
    PyErr_SetString(PyExc_SystemError, "too many buffers");
    return NULL;
  }
  Py_buffer *view = &vs->v[vs->n];
  int flags = PyBUF_SIMPLE | (writable ? PyBUF_WRITABLE : 0);
  if (PyObject_GetBuffer(obj, view, flags) < 0) {
    return NULL;
  }
  vs->n += 1;
  if (view->len < need) {
    PyErr_Format(
      PyExc_ValueError,
      "%s buffer too short: %zd bytes, need %zd",
      what,
      view->len,
      need
    );
    return NULL;
  }
  /* Codon reads from an address even for an empty slice; never hand it NULL. */
  static uint8_t empty_sentinel[8];
  return view->buf != NULL ? view->buf : (void *)empty_sentinel;
}

static PyObject *
raise_kernel_code(const char *kernel, long code)
{
  if (code == ERR_NAME_CAP) {
    return PyErr_Format(
      PyExc_OverflowError, "%s: distinct-name table capacity exceeded", kernel
    );
  }
  return PyErr_Format(PyExc_ValueError, "%s: kernel error code %ld", kernel, code);
}

static PyObject *
copy_count_rows_py(PyObject *self, PyObject *args)
{
  PyObject *buf_obj;
  int has_owned;
  if (!PyArg_ParseTuple(args, "Op:copy_count_rows", &buf_obj, &has_owned)) {
    return NULL;
  }
  views vs = {.n = 0};
  const uint8_t *buf = take_buffer(&vs, buf_obj, 0, 0, "copy stream");
  if (buf == NULL) {
    release_views(&vs);
    return NULL;
  }
  long rc = copy_count_rows_c(buf, (long)vs.v[0].len, has_owned);
  release_views(&vs);
  if (rc < 0) {
    return raise_kernel_code("copy_count_rows", rc);
  }
  return PyLong_FromLong(rc);
}

static PyObject *
copy_decode_py(PyObject *self, PyObject *args)
{
  PyObject *buf_obj, *o_time, *o_host, *o_type, *o_event, *o_dev, *o_owned;
  PyObject *o_value, *o_arc, *o_delta, *o_ncol, *o_noff, *o_nlen;
  int has_owned;
  Py_ssize_t nrows, name_cap;
  if (!PyArg_ParseTuple(
        args,
        "OpnOOOOOOOOOnOOO:copy_decode",
        &buf_obj,
        &has_owned,
        &nrows,
        &o_time,
        &o_host,
        &o_type,
        &o_event,
        &o_dev,
        &o_owned,
        &o_value,
        &o_arc,
        &o_delta,
        &name_cap,
        &o_ncol,
        &o_noff,
        &o_nlen
      )) {
    return NULL;
  }
  if (nrows < 0 || name_cap < 0) {
    PyErr_SetString(PyExc_ValueError, "nrows and name_cap must be >= 0");
    return NULL;
  }
  views vs = {.n = 0};
  const uint8_t *buf = take_buffer(&vs, buf_obj, 0, 0, "copy stream");
  int64_t *t = take_buffer(&vs, o_time, 1, nrows * 8, "time");
  int32_t *h = take_buffer(&vs, o_host, 1, nrows * 4, "host");
  int32_t *ty = take_buffer(&vs, o_type, 1, nrows * 4, "type");
  int32_t *ev = take_buffer(&vs, o_event, 1, nrows * 4, "event");
  int32_t *dv = take_buffer(&vs, o_dev, 1, nrows * 4, "dev");
  uint8_t *ow = take_buffer(&vs, o_owned, 1, nrows, "owned");
  double *va = take_buffer(&vs, o_value, 1, nrows * 8, "value");
  double *ar = take_buffer(&vs, o_arc, 1, nrows * 8, "arc");
  double *de = take_buffer(&vs, o_delta, 1, nrows * 8, "delta");
  int64_t *nc = take_buffer(&vs, o_ncol, 1, name_cap * 8, "name_col");
  int64_t *no = take_buffer(&vs, o_noff, 1, name_cap * 8, "name_off");
  int64_t *nl = take_buffer(&vs, o_nlen, 1, name_cap * 8, "name_len");
  if (PyErr_Occurred()) {
    release_views(&vs);
    return NULL;
  }
  long rc = copy_decode_c(
    buf,
    (long)vs.v[0].len,
    has_owned,
    (long)nrows,
    t,
    h,
    ty,
    ev,
    dv,
    ow,
    va,
    ar,
    de,
    (long)name_cap,
    nc,
    no,
    nl
  );
  release_views(&vs);
  if (rc < 0) {
    return raise_kernel_code("copy_decode", rc);
  }
  return PyLong_FromLong(rc);
}

static PyObject *
group_reduce_py(PyObject *self, PyObject *args)
{
  PyObject *o_keys, *o_values, *o_mask, *o_scratch, *o_okeys, *o_ocnt,
    *o_osum, *o_omax;
  Py_ssize_t n;
  int has_mask;
  if (!PyArg_ParseTuple(
        args,
        "nOOpOOOOOO:group_reduce",
        &n,
        &o_keys,
        &o_values,
        &has_mask,
        &o_mask,
        &o_scratch,
        &o_okeys,
        &o_ocnt,
        &o_osum,
        &o_omax
      )) {
    return NULL;
  }
  if (n < 0) {
    PyErr_SetString(PyExc_ValueError, "n must be >= 0");
    return NULL;
  }
  views vs = {.n = 0};
  const int64_t *keys = take_buffer(&vs, o_keys, 0, n * 8, "keys");
  const double *values = take_buffer(&vs, o_values, 0, n * 8, "values");
  const uint8_t *mask =
    take_buffer(&vs, o_mask, 0, has_mask ? n : 0, "mask");
  int64_t *scratch =
    take_buffer(&vs, o_scratch, 1, (4 * n + RADIX_BUCKETS) * 8, "scratch");
  int64_t *ok = take_buffer(&vs, o_okeys, 1, n * 8, "out_keys");
  int64_t *oc = take_buffer(&vs, o_ocnt, 1, n * 8, "out_cnt");
  double *os = take_buffer(&vs, o_osum, 1, n * 8, "out_sum");
  double *om = take_buffer(&vs, o_omax, 1, n * 8, "out_max");
  if (PyErr_Occurred()) {
    release_views(&vs);
    return NULL;
  }
  long rc = group_reduce_c(
    (long)n, keys, values, has_mask, mask, scratch, ok, oc, os, om
  );
  release_views(&vs);
  return PyLong_FromLong(rc);
}

static PyObject *
grid_mean_py(PyObject *self, PyObject *args)
{
  PyObject *o_cells, *o_values, *o_cnt, *o_grid;
  Py_ssize_t n, size;
  if (!PyArg_ParseTuple(
        args, "nOOnOO:grid_mean", &n, &o_cells, &o_values, &size, &o_cnt, &o_grid
      )) {
    return NULL;
  }
  if (n < 0 || size < 0) {
    PyErr_SetString(PyExc_ValueError, "n and size must be >= 0");
    return NULL;
  }
  views vs = {.n = 0};
  const int64_t *cells = take_buffer(&vs, o_cells, 0, n * 8, "cells");
  const double *values = take_buffer(&vs, o_values, 0, n * 8, "values");
  int64_t *cnt = take_buffer(&vs, o_cnt, 1, size * 8, "cnt_scratch");
  double *grid = take_buffer(&vs, o_grid, 1, size * 8, "out_grid");
  if (PyErr_Occurred()) {
    release_views(&vs);
    return NULL;
  }
  long rc = grid_mean_c((long)n, cells, values, (long)size, cnt, grid);
  release_views(&vs);
  if (rc < 0) {
    return raise_kernel_code("grid_mean", rc);
  }
  return PyLong_FromLong(rc);
}

static PyObject *
join_sorted_py(PyObject *self, PyObject *args)
{
  PyObject *o_left, *o_right, *o_li, *o_ri;
  Py_ssize_t nl, nr;
  if (!PyArg_ParseTuple(
        args, "nOnOOO:join_sorted", &nl, &o_left, &nr, &o_right, &o_li, &o_ri
      )) {
    return NULL;
  }
  if (nl < 0 || nr < 0) {
    PyErr_SetString(PyExc_ValueError, "nl and nr must be >= 0");
    return NULL;
  }
  Py_ssize_t cap = nl < nr ? nl : nr;
  views vs = {.n = 0};
  const int64_t *left = take_buffer(&vs, o_left, 0, nl * 8, "left");
  const int64_t *right = take_buffer(&vs, o_right, 0, nr * 8, "right");
  int64_t *li = take_buffer(&vs, o_li, 1, cap * 8, "out_li");
  int64_t *ri = take_buffer(&vs, o_ri, 1, cap * 8, "out_ri");
  if (PyErr_Occurred()) {
    release_views(&vs);
    return NULL;
  }
  long rc = join_sorted_c((long)nl, left, (long)nr, right, li, ri);
  release_views(&vs);
  return PyLong_FromLong(rc);
}

static PyMethodDef metrics_methods[] = {
  {
    "copy_count_rows",
    copy_count_rows_py,
    METH_VARARGS,
    "copy_count_rows(stream, has_owned) -> tuple count of a binary COPY stream",
  },
  {
    "copy_decode",
    copy_decode_py,
    METH_VARARGS,
    "copy_decode(stream, has_owned, nrows, time, host, type, event, dev, "
    "owned, value, arc, delta, name_cap, name_col, name_off, name_len) -> "
    "distinct name count (OverflowError when name_cap is too small)",
  },
  {
    "group_reduce",
    group_reduce_py,
    METH_VARARGS,
    "group_reduce(n, keys, values, has_mask, mask, scratch, out_keys, "
    "out_cnt, out_sum, out_max) -> group count (scratch: 4*n + 65536 int64)",
  },
  {
    "grid_mean",
    grid_mean_py,
    METH_VARARGS,
    "grid_mean(n, cells, values, size, cnt_scratch, out_grid) -> rows used",
  },
  {
    "join_sorted",
    join_sorted_py,
    METH_VARARGS,
    "join_sorted(nl, left, nr, right, out_li, out_ri) -> hit count",
  },
  {NULL, NULL, 0, NULL},
};

static int
hpcperfstats_codon_metrics_exec(PyObject *module)
{
  return PyModule_AddFunctions(module, metrics_methods);
}

static PyModuleDef_Slot metrics_slots[] = {
  {Py_mod_exec, (void *)hpcperfstats_codon_metrics_exec},
#ifdef Py_GIL_DISABLED
  {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
  {0, NULL},
};

static struct PyModuleDef metrics_module = {
  PyModuleDef_HEAD_INIT,
  .m_name = "hpcperfstats_codon_metrics",
  .m_doc = "Codon update_metrics columnar kernels (C-ABI, multi-phase FT init, "
           "GIL_NOT_USED)",
  .m_size = 0,
  .m_slots = metrics_slots,
};

PyMODINIT_FUNC
PyInit_hpcperfstats_codon_metrics(void)
{
  return PyModuleDef_Init(&metrics_module);
}
