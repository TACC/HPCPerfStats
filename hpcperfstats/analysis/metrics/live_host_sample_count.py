"""PostgreSQL correlated subquery: live per-host distinct sample times (host_data).

Used when deciding whether to re-run metrics after new samples arrive. The outer
row is the accounting ``job_data`` row; correlation uses quoted table/column
names in SQL. Only the site FQDN suffix is a bound parameter (never ``OuterRef``
in params — drivers cannot adapt those).
"""
from django.db.models import IntegerField
from django.db.models.expressions import Expression

from hpcperfstats.site.machine.models import host_data, job_data


class LiveDistinctHostTimeCount(Expression):
  """Scalar subquery: ``SUM`` over job hosts of ``COUNT(DISTINCT time)`` in host_data.

  Correlates to the outer ``job_data`` row via ``start_time``, ``end_time``, and
  ``host_list`` (``unnest`` + FQDN suffix). PostgreSQL only.
  """

  allowed_default = True

  def __init__(self, host_suffix, *, outer_model=None, output_field=None):
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
    self.host_suffix = host_suffix
    self.outer_model = outer_model or job_data

  def __repr__(self):
    return "{}({!r})".format(self.__class__.__name__, self.host_suffix)

  def get_group_by_cols(self):
    return [self]

  def resolve_expression(
      self,
      query=None,
      allow_joins=True,
      reuse=None,
      summarize=False,
      for_save=False,
  ):
    # Same idea as RawSQL: ensure parent MTI columns used in SQL are resolved.
    if query.model:
      sql_lower = self._resolve_hint_sql().lower()
      for parent in query.model._meta.all_parents:
        for parent_field in parent._meta.local_fields:
          if parent_field.column.lower() in sql_lower:
            query.resolve_ref(
                parent_field.name, allow_joins, reuse, summarize
            )
            break
    return super().resolve_expression(
        query, allow_joins, reuse, summarize, for_save
    )

  def _resolve_hint_sql(self):
    """Column names referenced for ``resolve_expression`` (unquoted, lowercase)."""
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in ("start_time", "end_time", "host_list")
    )

  def as_sql(self, compiler, connection):
    if connection.vendor != "postgresql":
      raise NotImplementedError(
          "{} requires PostgreSQL (got {!r})".format(
              self.__class__.__name__, connection.vendor
          )
      )
    ops = connection.ops
    jt = ops.quote_name(self.outer_model._meta.db_table)
    st = ops.quote_name("start_time")
    et = ops.quote_name("end_time")
    hl = ops.quote_name("host_list")
    ht = ops.quote_name(host_data._meta.db_table)
    inner = (
        "SELECT COALESCE(SUM(ph.cnt), 0)::integer FROM ("
        "SELECT h.host, COUNT(DISTINCT h.time)::integer AS cnt "
        f"FROM {ht} h "
        f"WHERE h.time >= {jt}.{st} AND h.time <= {jt}.{et} AND h.host IN ("
        "SELECT (COALESCE(elem::text, '') || %s)::text "
        f"FROM unnest({jt}.{hl}) AS t(elem)) "
        "GROUP BY h.host) ph"
    )
    return "(%s)" % inner, [self.host_suffix]
