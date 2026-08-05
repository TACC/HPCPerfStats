"""
PostgreSQL SQL expressions matching persisted artifact input fingerprints.

Used by ``update_metrics._jobs_queryset`` to include jobs whose metrics are
complete but plot or job-detail artifacts are missing or stale (fingerprint
mismatch). Must stay aligned with ``compute_plot_input_fingerprint`` and
``compute_detail_input_fingerprint``.
"""
from __future__ import annotations

from typing import Any

import bokeh
from django.db.models import CharField, IntegerField
from django.db.models.expressions import Expression

from hpcperfstats.analysis.metrics.lib.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)
from hpcperfstats.site.lib.machine import job_detail_artifacts as detail_cfg
from hpcperfstats.site.lib.machine import job_plot_artifacts as plot_cfg
from hpcperfstats.site.lib.machine.models import job_data, job_detail_artifact


class PlotArtifactInputFingerprintHex(Expression):
  """
  ``encode(sha256(convert_to(canonical_json, 'UTF8'))), 'hex')`` for the outer.
  
  Attributes:
    _live_expr: Attribute.
    host_suffix: Attribute.
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    host_suffix: str,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      host_suffix (str): String for host suffix.
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> PlotArtifactInputFingerprintHex("x", None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = CharField(max_length=64)
    super().__init__(output_field=output_field)
    self.host_suffix = host_suffix
    self.outer_model = outer_model or job_data
    self._live_expr = live_distinct_host_time_count_expression(
        host_suffix, outer_model=self.outer_model
    )

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
      >>> PlotArtifactInputFingerprintHex().get_group_by_cols()  # doctest: +SKIP
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
      >>> PlotArtifactInputFingerprintHex()._resolve_hint_sql()  # doctest: +SKIP
    """
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in (
            "start_time",
            "end_time",
            "host_list",
            "jid",
            "metrics_distinct_time_count",
            "telemetry_first_time",
            "telemetry_last_time",
        )
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
      >>> PlotArtifactInputFingerprintHex().as_sql(None, None)  # doctest: +SKIP
    """
    if connection.vendor != "postgresql":
      raise NotImplementedError(
          "{} requires PostgreSQL (got {!r})".format(
              self.__class__.__name__, connection.vendor
          )
      )
    live_sql, live_params = self._live_expr.as_sql(compiler, connection)
    ops = connection.ops
    jt = ops.quote_name(self.outer_model._meta.db_table)
    st = ops.quote_name("start_time")
    et = ops.quote_name("end_time")
    hl = ops.quote_name("host_list")
    jcol = ops.quote_name("jid")
    mdc = ops.quote_name("metrics_distinct_time_count")
    tft = ops.quote_name("telemetry_first_time")
    tlt = ops.quote_name("telemetry_last_time")
    hosts_json = (
        f"COALESCE((SELECT json_agg(trim(elem) ORDER BY trim(elem)) "
        f"FILTER (WHERE trim(elem) <> '')::text "
        f"FROM unnest({jt}.{hl}) AS t(elem)), '[]')"
    )
    iso_ts = (
        "CASE WHEN {tbl}.{col} IS NULL THEN to_json(''::text)::text "
        "ELSE to_json(to_char({tbl}.{col} AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.USOF'))::text END"
    )
    et_iso = iso_ts.format(tbl=jt, col=et)
    st_iso = iso_ts.format(tbl=jt, col=st)
    tft_iso = iso_ts.format(tbl=jt, col=tft)
    tlt_iso = iso_ts.format(tbl=jt, col=tlt)
    inner = (
        f"'{{\"artifact_schema\":' || %s::text || "
        f"',\"bokeh\":' || to_json(%s::text)::text || "
        f"',\"et\":' || {et_iso} || "
        f"',\"hosts\":' || {hosts_json} || "
        f"',\"jid\":' || to_json(trim(both from {jt}.{jcol}::text))::text || "
        f"',\"live_distinct\":' || ({live_sql})::text || "
        f"',\"mdc\":' || (CASE WHEN {jt}.{mdc} IS NULL THEN 'null' "
        f"ELSE {jt}.{mdc}::text END) || "
        f"',\"st\":' || {st_iso} || "
        f"',\"tft\":' || {tft_iso} || "
        f"',\"tlt\":' || {tlt_iso} || '}}'"
    )
    sql = "encode(sha256(convert_to(({})::text, 'UTF8')), 'hex')".format(inner)
    params = [plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION, bokeh.__version__]
    params.extend(live_params)
    return sql, params


class DetailArtifactInputFingerprintHex(Expression):
  """
  SHA256 hex for detail artifacts (``compute_detail_input_fingerprint``).
  
  Attributes:
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> DetailArtifactInputFingerprintHex(None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = CharField(max_length=64)
    super().__init__(output_field=output_field)
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
    return "{}()".format(self.__class__.__name__)

  def get_group_by_cols(self) -> Any:
    """
    Return the group by cols.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> DetailArtifactInputFingerprintHex().get_group_by_cols()
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
      >>> DetailArtifactInputFingerprintHex()._resolve_hint_sql()
    """
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in (
            "start_time",
            "end_time",
            "jid",
            "metrics_distinct_time_count",
        )
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
      >>> DetailArtifactInputFingerprintHex().as_sql(None, None)  # doctest: +SKIP
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
    mdc = ops.quote_name("metrics_distinct_time_count")
    aschema = detail_cfg.APP_DETAIL_ARTIFACT_SCHEMA_VERSION
    iso_ts = (
        "CASE WHEN {tbl}.{col} IS NULL THEN to_json(''::text)::text "
        "ELSE to_json(to_char({tbl}.{col} AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.USOF'))::text END"
    )
    et_iso = iso_ts.format(tbl=jt, col=et)
    st_iso = iso_ts.format(tbl=jt, col=st)
    # Keep key order + formatting aligned with
    # ``compute_detail_input_fingerprint`` / ``_fsio_metrics_fingerprint_map``.
    fsio_names = detail_cfg._FSIO_FINGERPRINT_METRIC_NAMES
    fsio_parts = []
    for name in fsio_names:
      fsio_parts.append(
          f"'{name}', COALESCE(("
          f"SELECT CASE WHEN m.value IS NULL THEN ''::text "
          f"ELSE to_char(m.value, 'FM999999999990.000000') END "
          f"FROM metrics_data m "
          f"WHERE m.jid = {jt}.{jcol} AND m.metric = '{name}' "
          f"LIMIT 1), '')"
      )
    fsio_obj = "json_build_object(" + ", ".join(fsio_parts) + ")::text"
    inner = (
        f"'{{\"artifact_schema\":' || %s::text || "
        f"',\"end_time\":' || {et_iso} || "
        f"',\"fsio_metrics\":' || {fsio_obj} || "
        f"',\"jid\":' || to_json(trim(both from {jt}.{jcol}::text))::text || "
        f"',\"metrics_distinct_time_count\":' || "
        f"to_json(COALESCE({jt}.{mdc}::text, ''))::text || "
        f"',\"start_time\":' || {st_iso} || '}}'"
    )
    sql = "encode(sha256(convert_to(({})::text, 'UTF8')), 'hex')".format(inner)
    return sql, [aschema]


class HostDataSchemaKeyCount(Expression):
  """
  Scalar: number of keys in ``job_data.host_data_schema_json`` (PostgreSQL.
  
  Attributes:
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> HostDataSchemaKeyCount(None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
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
    return "{}()".format(self.__class__.__name__)

  def get_group_by_cols(self) -> Any:
    """
    Return the group by cols.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> HostDataSchemaKeyCount().get_group_by_cols()  # doctest: +SKIP
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
      query.resolve_ref(
          "host_data_schema_json", allow_joins, reuse, summarize
      )
    return super().resolve_expression(
        query, allow_joins, reuse, summarize, for_save
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
      >>> HostDataSchemaKeyCount().as_sql(None, None)  # doctest: +SKIP
    """
    if connection.vendor != "postgresql":
      raise NotImplementedError(
          "{} requires PostgreSQL (got {!r})".format(
              self.__class__.__name__, connection.vendor
          )
      )
    ops = connection.ops
    jt = ops.quote_name(self.outer_model._meta.db_table)
    sch = ops.quote_name("host_data_schema_json")
    sql = (
        f"(SELECT COUNT(*)::integer FROM "
        f"jsonb_object_keys(COALESCE({jt}.{sch}::jsonb, '{{}}'::jsonb)) AS _k(key))"
    )
    return sql, []


class TypeDetailFreshFingerprintRowCount(Expression):
  """
  Count ``type_detail`` rows whose scope is in the job schema and FP matches.
  
  Attributes:
    outer_model: Attribute.
  """

  allowed_default = True

  def __init__(
    self,
    *,
    outer_model: Any | None = None,
    output_field: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      outer_model (Any | None): One of ``Any``, ``None``.
      output_field (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> TypeDetailFreshFingerprintRowCount(None, None)  # doctest: +SKIP
    """
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
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
    return "{}()".format(self.__class__.__name__)

  def get_group_by_cols(self) -> Any:
    """
    Return the group by cols.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> TypeDetailFreshFingerprintRowCount().get_group_by_cols()
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
      for name in (
          "jid",
          "host_data_schema_json",
          "start_time",
          "end_time",
          "metrics_distinct_time_count",
      ):
        query.resolve_ref(name, allow_joins, reuse, summarize)
    return super().resolve_expression(
        query, allow_joins, reuse, summarize, for_save
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
      >>> TypeDetailFreshFingerprintRowCount().as_sql(None, None)
    """
    if connection.vendor != "postgresql":
      raise NotImplementedError(
          "{} requires PostgreSQL (got {!r})".format(
              self.__class__.__name__, connection.vendor
          )
      )
    ops = connection.ops
    jd = ops.quote_name(self.outer_model._meta.db_table)
    jdc = ops.quote_name("jid")
    sch = ops.quote_name("host_data_schema_json")
    dt = ops.quote_name(job_detail_artifact._meta.db_table)
    kind_c = ops.quote_name("artifact_kind")
    fp_c = ops.quote_name("input_fingerprint")
    scope_c = ops.quote_name("artifact_scope")
    jid_c = ops.quote_name(job_detail_artifact._meta.get_field("jid").column)
    detail_fp = DetailArtifactInputFingerprintHex(outer_model=self.outer_model)
    fp_sql, fp_params = detail_fp.as_sql(compiler, connection)
    sql = (
        f"(SELECT COUNT(*)::integer FROM {dt} t WHERE "
        f"t.{jid_c} = {jd}.{jdc} "
        f"AND t.{kind_c} = %s "
        f"AND t.{fp_c} = ({fp_sql}) "
        f"AND t.{scope_c} IN ("
        f"SELECT jsonb_object_keys(COALESCE({jd}.{sch}::jsonb, '{{}}'::jsonb))"
        f"))"
    )
    params = [detail_cfg.ARTIFACT_KIND_TYPE_DETAIL]
    params.extend(fp_params)
    return sql, params
