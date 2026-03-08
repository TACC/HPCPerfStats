"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
from hpcperfstats.analysis.metrics.update_metrics import _iter_chunked_pks


def test_iter_chunked_pks_empty_queryset():
  """_iter_chunked_pks yields nothing for empty pk iterator."""
  class EmptyQs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=1):
      return iter([])

  qs = EmptyQs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert chunks == []


def test_iter_chunked_pks_single_chunk():
  """_iter_chunked_pks yields one (pk_list, total) when pks fit in one chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=10):
      return iter([1, 2, 3])

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 10))
  assert len(chunks) == 1
  assert chunks[0][0] == [1, 2, 3]
  assert chunks[0][1] == 3


def test_iter_chunked_pks_multiple_chunks():
  """_iter_chunked_pks yields (pk_list, total_so_far) for each chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=2):
      return iter([10, 20, 30, 40, 50])

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert len(chunks) == 3
  assert chunks[0] == ([10, 20], 2)
  assert chunks[1] == ([30, 40], 4)
  assert chunks[2] == ([50], 5)
