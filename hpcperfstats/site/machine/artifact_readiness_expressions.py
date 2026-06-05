"""PostgreSQL SQL expressions matching persisted artifact input fingerprints.

Used by ``update_metrics._jobs_queryset`` to include jobs whose metrics are
complete but plot or job-detail artifacts are missing or stale (fingerprint
mismatch). Must stay aligned with ``compute_plot_input_fingerprint`` and
``compute_detail_input_fingerprint``.
"""
from __future__ import annotations

import bokeh
from django.db.models import CharField, IntegerField
from django.db.models.expressions import Expression

from hpcperfstats.analysis.metrics.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)
from hpcperfstats.site.machine import job_detail_artifacts as detail_cfg
from hpcperfstats.site.machine import job_plot_artifacts as plot_cfg
from hpcperfstats.site.machine.models import job_data, job_detail_artifact


class PlotArtifactInputFingerprintHex(Expression):
  """``encode(sha256(convert_to(canonical_json, 'UTF8'))), 'hex')`` for the outer ``job_data`` row."""

  allowed_default = True

  def __init__(self, host_suffix: str, *, outer_model=None, output_field=None):
    if output_field is None:
      output_field = CharField(max_length=64)
    super().__init__(output_field=output_field)
    self.host_suffix = host_suffix
    self.outer_model = outer_model or job_data
    self._live_expr = live_distinct_host_time_count_expression(
        host_suffix, outer_model=self.outer_model
    )

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
    meta = self.outer_model._meta
    return " ".join(
        meta.get_field(name).column
        for name in (
            "start_time",
            "end_time",
            "host_list",
            "jid",
            "metrics_distinct_time_count",
        )
    )

  def as_sql(self, compiler, connection):
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
    inner = (
        f"'{{\"artifact_schema\":' || %s::text || "
        f"',\"bokeh\":' || to_json(%s::text)::text || "
        f"',\"et\":' || {et_iso} || "
        f"',\"hosts\":' || {hosts_json} || "
        f"',\"jid\":' || to_json(trim(both from {jt}.{jcol}::text))::text || "
        f"',\"live_distinct\":' || ({live_sql})::text || "
        f"',\"mdc\":' || (CASE WHEN {jt}.{mdc} IS NULL THEN 'null' "
        f"ELSE {jt}.{mdc}::text END) || "
        f"',\"st\":' || {st_iso} || '}}'"
    )
    sql = "encode(sha256(convert_to(({})::text, 'UTF8')), 'hex')".format(inner)
    params = [plot_cfg.APP_PLOT_ARTIFACT_SCHEMA_VERSION, bokeh.__version__]
    params.extend(live_params)
    return sql, params


class DetailArtifactInputFingerprintHex(Expression):
  """SHA256 hex for detail artifacts (``compute_detail_input_fingerprint``)."""

  allowed_default = True

  def __init__(self, *, outer_model=None, output_field=None):
    if output_field is None:
      output_field = CharField(max_length=64)
    super().__init__(output_field=output_field)
    self.outer_model = outer_model or job_data

  def __repr__(self):
    return "{}()".format(self.__class__.__name__)

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
    jcol = ops.quote_name("jid")
    mdc = ops.quote_name("metrics_distinct_time_count")
    aschema = detail_cfg.APP_DETAIL_ARTIFACT_SCHEMA_VERSION
    inner = (
        f"'{{\"artifact_schema\":' || %s::text || "
        f"',\"end_time\":' || (CASE WHEN {jt}.{et} IS NULL THEN to_json(''::text)::text "
        f"ELSE to_json({jt}.{et})::text END) || "
        f"',\"jid\":' || to_json(trim(both from {jt}.{jcol}::text))::text || "
        f"',\"metrics_distinct_time_count\":' || "
        f"to_json(COALESCE({jt}.{mdc}::text, ''))::text || "
        f"',\"start_time\":' || (CASE WHEN {jt}.{st} IS NULL THEN to_json(''::text)::text "
        f"ELSE to_json({jt}.{st})::text END) || '}}'"
    )
    sql = "encode(sha256(convert_to(({})::text, 'UTF8')), 'hex')".format(inner)
    return sql, [aschema]


class HostDataSchemaKeyCount(Expression):
  """Scalar: number of keys in ``job_data.host_data_schema_json`` (PostgreSQL ``jsonb``)."""

  allowed_default = True

  def __init__(self, *, outer_model=None, output_field=None):
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
    self.outer_model = outer_model or job_data

  def __repr__(self):
    return "{}()".format(self.__class__.__name__)

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
    if query.model:
      query.resolve_ref(
          "host_data_schema_json", allow_joins, reuse, summarize
      )
    return super().resolve_expression(
        query, allow_joins, reuse, summarize, for_save
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
    sch = ops.quote_name("host_data_schema_json")
    sql = (
        f"(SELECT COUNT(*)::integer FROM "
        f"jsonb_object_keys(COALESCE({jt}.{sch}::jsonb, '{{}}'::jsonb)) AS _k(key))"
    )
    return sql, []


class TypeDetailFreshFingerprintRowCount(Expression):
  """Count ``type_detail`` rows whose scope is in the job schema and FP matches."""

  allowed_default = True

  def __init__(self, *, outer_model=None, output_field=None):
    if output_field is None:
      output_field = IntegerField()
    super().__init__(output_field=output_field)
    self.outer_model = outer_model or job_data

  def __repr__(self):
    return "{}()".format(self.__class__.__name__)

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

  def as_sql(self, compiler, connection):
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
