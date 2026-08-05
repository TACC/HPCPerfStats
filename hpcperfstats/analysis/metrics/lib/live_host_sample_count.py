"""
PostgreSQL correlated subquery: live per-host distinct sample times (host_data).

Used when deciding whether to re-run metrics after new samples arrive. The outer
row is the accounting ``job_data`` row; correlation uses quoted table/column
names in SQL. Only the site FQDN suffix is a bound parameter for the legacy
``host_list`` path (never ``OuterRef`` in params — drivers cannot adapt those).
"""
from __future__ import annotations

from typing import Any

from django.db.models import IntegerField
from django.db.models.expressions import Expression

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.site.lib.machine.models import host_data, job_data


class LiveDistinctHostTimeCount(Expression):
  """
  Scalar subquery: ``SUM`` over job hosts of ``COUNT(DISTINCT time)`` in
    host_data.
  
  Correlates to the outer ``job_data`` row via ``start_time``, ``end_time``, and
  ``host_list`` (``unnest`` + FQDN suffix). PostgreSQL only.
  
  Attributes:
    host_suffix: Attribute.
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    host_suffix: Any,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      host_suffix (Any): Host suffix passed to this helper.
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> LiveDistinctHostTimeCount(None, None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
    self.host_suffix = host_suffix
    self.outer_model = outer_model or job_data

  def __repr__(self) -> Any:
    """
    Return the official string representation.
    
    Returns:
      Any: Open return polymorphism from ``__repr__``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __repr__()  # doctest: +SKIP
    """
    return "{}({!r})".format(self.__class__.__name__, self.host_suffix)

  def get_group_by_cols(self) -> Any:
    """
    Return the group by cols.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> LiveDistinctHostTimeCount().get_group_by_cols()  # doctest: +SKIP
    """
    return [self]

  def resolve_expression(
    self,
    query: Any | None = None,
    allow_joins: bool = True,
    reuse: Any | None = None,
    summarize: bool = False,
    for_save: bool = False,
  ) -> Any:
    """
    Resolve the expression.
    
    Args:
      query (Any | None): One of ``Any``, ``None``.
      allow_joins (bool): Boolean flag for allow joins.
      reuse (Any | None): One of ``Any``, ``None``.
      summarize (bool): Boolean flag for summarize.
      for_save (bool): Boolean flag for for save.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> resolve_expression(0)  # doctest: +SKIP
    """
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

  def _resolve_hint_sql(self) -> Any:
    """
    Internal helper to resolve the hint sql.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> LiveDistinctHostTimeCount()._resolve_hint_sql()  # doctest: +SKIP
    """
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in ("start_time", "end_time", "host_list")
    )

  def as_sql(self, compiler: Any, connection: Any) -> Any:
    """
    As sql.
    
    Args:
      compiler (Any): Compiler passed to this helper.
      connection (Any): Live handle (pool, client, or connection).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      NotImplementedError: Raised when ``as_sql`` hits a
      ``NotImplementedError`` failure path.
    
    Examples:
      >>> LiveDistinctHostTimeCount().as_sql(None, None)  # doctest: +SKIP
    """
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


class LiveJidScopedDistinctHostTimeCount(Expression):
  """
  Like ``LiveDistinctHostTimeCount`` but scopes rows by ``host_data.jid`` (no
    ``unnest``).
  
  Sums ``COUNT(DISTINCT time)`` per host for rows matching the outer job's
  ``jid`` and ``[start_time, end_time]``. ``host_suffix`` is accepted for API
  compatibility but is not used in SQL.
  
  Attributes:
    host_suffix: Attribute.
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    host_suffix: Any,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      host_suffix (Any): Host suffix passed to this helper.
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> LiveJidScopedDistinctHostTimeCount(None, None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
    self.host_suffix = host_suffix
    self.outer_model = outer_model or job_data

  def __repr__(self) -> Any:
    """
    Return the official string representation.
    
    Returns:
      Any: Open return polymorphism from ``__repr__``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __repr__()  # doctest: +SKIP
    """
    return "{}({!r})".format(self.__class__.__name__, self.host_suffix)

  def get_group_by_cols(self) -> Any:
    """
    Return the group by cols.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> LiveJidScopedDistinctHostTimeCount().get_group_by_cols()
    """
    return [self]

  def resolve_expression(
    self,
    query: Any | None = None,
    allow_joins: bool = True,
    reuse: Any | None = None,
    summarize: bool = False,
    for_save: bool = False,
  ) -> Any:
    """
    Resolve the expression.
    
    Args:
      query (Any | None): One of ``Any``, ``None``.
      allow_joins (bool): Boolean flag for allow joins.
      reuse (Any | None): One of ``Any``, ``None``.
      summarize (bool): Boolean flag for summarize.
      for_save (bool): Boolean flag for for save.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> resolve_expression(0)  # doctest: +SKIP
    """
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

  def _resolve_hint_sql(self) -> Any:
    """
    Internal helper to resolve the hint sql.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> LiveJidScopedDistinctHostTimeCount()._resolve_hint_sql()
    """
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in ("start_time", "end_time", "jid")
    )

  def as_sql(self, compiler: Any, connection: Any) -> Any:
    """
    As sql.
    
    Args:
      compiler (Any): Compiler passed to this helper.
      connection (Any): Live handle (pool, client, or connection).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      NotImplementedError: Raised when ``as_sql`` hits a
      ``NotImplementedError`` failure path.
    
    Examples:
      >>> LiveJidScopedDistinctHostTimeCount().as_sql(None, None)
    """
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
    jcol = ops.quote_name("jid")
    ht = ops.quote_name(host_data._meta.db_table)
    hj = ops.quote_name("jid")
    inner = (
        "SELECT COALESCE(SUM(ph.cnt), 0)::integer FROM ("
        "SELECT h.host, COUNT(DISTINCT h.time)::integer AS cnt "
        f"FROM {ht} h "
        f"WHERE h.{hj} = {jt}.{jcol} "
        f"AND h.time >= {jt}.{st} AND h.time <= {jt}.{et} "
        "GROUP BY h.host) ph"
    )
    return "(%s)" % inner, []


def live_distinct_host_time_count_expression(
  host_suffix: Any,
  *,
  outer_model: Any | None = None,
) -> Any:
  """
  Return live-distinct annotation: legacy ``host_list`` or default jid-scoped.
  
    SQL.
  
  Args:
    host_suffix (Any): Host suffix passed to this helper.
    outer_model (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> live_distinct_host_time_count_expression(None, None)  # doctest: +SKIP
  """
  if cfg.get_live_distinct_use_legacy_hostlist():
    return LiveDistinctHostTimeCount(host_suffix, outer_model=outer_model)
  return LiveJidScopedDistinctHostTimeCount(host_suffix, outer_model=outer_model)
