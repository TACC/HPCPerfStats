"""
PostgreSQL SQL expressions matching persisted artifact input fingerprints.

Used by ``update_metrics._jobs_queryset`` and job-list Performance Data to
detect missing or stale plot/detail artifacts (fingerprint mismatch). Must stay
aligned with ``compute_plot_input_fingerprint`` and
``compute_detail_input_fingerprint``.
"""
from __future__ import annotations

from typing import Any

import bokeh
from django.db import connections
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.expressions import Expression
from django.db.models.functions import Coalesce

from hpcperfstats.site.lib.machine import job_detail_artifacts as detail_cfg
from hpcperfstats.site.lib.machine import job_plot_artifacts as plot_cfg
from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_NORMAL,
)
from hpcperfstats.site.lib.machine.models import (
    job_data,
    job_detail_artifact,
    job_plot_artifact,
)


class PlotArtifactInputFingerprintHex(Expression):
  """
  ``encode(sha256(convert_to(canonical_json, 'UTF8'))), 'hex')`` for plot.

  ``live_distinct`` in the canonical JSON is ``COALESCE(metrics_distinct_time_count, 0)``
  (persisted only — no request-time ``host_data`` COUNT).
  
  Attributes:
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
      host_suffix (str): String for host suffix (unused; kept for signature parity).
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
    # live_distinct key kept for wire compatibility; value is persisted mdc only.
    mdc_num = f"COALESCE({jt}.{mdc}, 0)"
    inner = (
        f"'{{\"artifact_schema\":' || %s::text || "
        f"',\"bokeh\":' || to_json(%s::text)::text || "
        f"',\"et\":' || {et_iso} || "
        f"',\"hosts\":' || {hosts_json} || "
        f"',\"jid\":' || to_json(trim(both from {jt}.{jcol}::text))::text || "
        f"',\"live_distinct\":' || ({mdc_num})::text || "
        f"',\"mdc\":' || (CASE WHEN {jt}.{mdc} IS NULL THEN 'null' "
        f"ELSE {jt}.{mdc}::text END) || "
        f"',\"st\":' || {st_iso} || "
        f"',\"tft\":' || {tft_iso} || "
        f"',\"tlt\":' || {tlt_iso} || '}}'"
    )
    sql = "encode(sha256(convert_to(({})::text, 'UTF8')), 'hex')".format(inner)
    params = [plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION, bokeh.__version__]
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


def annotate_job_plots_artifacts_ready(queryset: Any, host_suffix: str) -> Any:
  """
  Annotate ``plots_artifacts_ready`` (and PG fingerprint helpers).

  On PostgreSQL, ready means FP-matching plot rows for all ``JOB_PLOT_KINDS``,
  fresh job_detail + multiprecision_mix rows, and type-detail coverage. On
  other vendors, annotate ``plots_artifacts_ready=False`` (fail closed).

  Args:
    queryset: ``job_data`` queryset.
    host_suffix: FQDN suffix (e.g. ``.cluster.example``) for plot FP SQL.

  Returns:
    Annotated queryset.

  Examples:
    >>> annotate_job_plots_artifacts_ready(None, ".x")  # doctest: +SKIP
  """
  if connections["default"].vendor != "postgresql":
    return queryset.annotate(
        plots_artifacts_ready=Value(False, output_field=BooleanField()),
    )
  int0 = Value(0, output_field=IntegerField())
  plot_fp_match_sq = Subquery(
      job_plot_artifact.objects.filter(
          jid_id=OuterRef("jid"),
          layout=JOB_PLOT_LAYOUT_NORMAL,
          plot_kind__in=list(JOB_PLOT_KINDS),
          input_fingerprint=OuterRef("expected_plot_input_fp"),
      )
      .values("jid_id")
      .annotate(c=Count("id"))
      .values("c")[:1],
      output_field=IntegerField(),
  )
  job_detail_ok = Exists(
      job_detail_artifact.objects.filter(
          jid_id=OuterRef("jid"),
          artifact_kind=detail_cfg.ARTIFACT_KIND_JOB_DETAIL,
          artifact_scope="",
          input_fingerprint=OuterRef("expected_detail_input_fp"),
      )
  )
  multiprecision_mix_ok = Exists(
      job_detail_artifact.objects.filter(
          jid_id=OuterRef("jid"),
          artifact_kind=detail_cfg.ARTIFACT_KIND_MULTIPRECISION_MIX,
          artifact_scope="",
          input_fingerprint=OuterRef("expected_detail_input_fp"),
      )
  )
  annotated = queryset.annotate(
      expected_plot_input_fp=PlotArtifactInputFingerprintHex(host_suffix),
      expected_detail_input_fp=DetailArtifactInputFingerprintHex(),
      schema_type_slot_count=HostDataSchemaKeyCount(),
      type_detail_fresh_row_count=TypeDetailFreshFingerprintRowCount(),
  )
  annotated = annotated.annotate(
      plot_fp_row_matches=Coalesce(plot_fp_match_sq, int0),
      job_detail_row_ok=job_detail_ok,
      multiprecision_mix_row_ok=multiprecision_mix_ok,
  )
  ready_q = (
      Q(plot_fp_row_matches__gte=len(JOB_PLOT_KINDS))
      & Q(job_detail_row_ok=True)
      & Q(multiprecision_mix_row_ok=True)
      & Q(schema_type_slot_count__lte=F("type_detail_fresh_row_count"))
  )
  return annotated.annotate(
      plots_artifacts_ready=Case(
          When(ready_q, then=Value(True)),
          default=Value(False),
          output_field=BooleanField(),
      ),
  )
