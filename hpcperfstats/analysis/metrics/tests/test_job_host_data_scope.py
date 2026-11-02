"""Unit tests for job_host_data_scope (shared-node co-tenant filter)."""

from unittest.mock import MagicMock, patch

from django.db.models import Q

from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  SHARED_HOST_WIDE_HELP_FOOTER,
  apply_gpu_affinity_dev_filter,
  apply_job_host_data_scope,
  apply_job_or_host_wide_scope,
  exclusive_hosts_cache_token,
  gpu_devs_for_job,
  host_wide_residual_ht_cache_key,
  is_job_scoped_host_data_type,
  job_dev_token,
  job_exclusive_hosts,
  job_host_data_scope_q,
  job_host_data_scope_sql,
  job_host_data_scope_sql_params,
  job_is_shared_node,
  show_shared_host_wide_help_footer,
  use_host_wide_residual_scope,
)


def test_job_dev_token_truncates_to_64():
  assert job_dev_token("42") == "j:42"
  assert len(job_dev_token("x" * 80)) == 64
  assert job_dev_token("x" * 80).startswith("j:")


def test_job_host_data_scope_q_or_jid_and_j_dev():
  q = job_host_data_scope_q("99")
  assert isinstance(q, Q)
  children = list(q.children)
  assert ("jid", "99") in children
  assert ("dev", "j:99") in children
  assert q.connector == Q.OR


def test_job_host_data_scope_sql_fragment():
  assert job_host_data_scope_sql() == ("(h.jid = %s OR h.dev = ('j:' || %s))")
  assert job_host_data_scope_sql(table_alias="") == (
    "(jid = %s OR dev = ('j:' || %s))"
  )


def test_apply_job_host_data_scope_filters_queryset():
  qs = MagicMock()
  qs.filter.return_value = "scoped"
  out = apply_job_host_data_scope(qs, "7")
  assert out == "scoped"
  args, _kwargs = qs.filter.call_args
  assert len(args) == 1
  assert isinstance(args[0], Q)
  assert ("jid", "7") in args[0].children
  assert ("dev", "j:7") in args[0].children


def test_apply_job_host_data_scope_empty_jid_passthrough():
  qs = MagicMock()
  assert apply_job_host_data_scope(qs, "") is qs
  qs.filter.assert_not_called()


def test_gpu_devs_for_job_none_when_no_affinity():
  with patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja:
    ja.objects.filter.return_value.values_list.return_value = []
    assert gpu_devs_for_job("1") is None


def test_gpu_devs_for_job_unions_lists():
  with patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja:
    qs = MagicMock()
    ja.objects.filter.return_value = qs
    qs.filter.return_value = qs
    qs.values_list.return_value = [["0", "1"], ["1", "2"]]
    assert gpu_devs_for_job("1", host="n0") == ["0", "1", "2"]


def test_gpu_devs_for_job_empty_list_when_affinity_no_gpus():
  with patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja:
    ja.objects.filter.return_value.values_list.return_value = [[], None]
    assert gpu_devs_for_job("1") == []


def test_apply_gpu_affinity_dev_filter_unrestricted():
  with patch(
    "hpcperfstats.analysis.metrics.lib.job_host_data_scope.gpu_devs_for_job",
    return_value=None,
  ):
    qs = MagicMock()
    assert apply_gpu_affinity_dev_filter(qs, "1") is qs
    assert apply_gpu_affinity_dev_filter(qs, "1", affinity_devs=None) is qs


def test_apply_gpu_affinity_dev_filter_restricts():
  qs = MagicMock()
  qs.filter.return_value = "gpu_scoped"
  out = apply_gpu_affinity_dev_filter(qs, "1", affinity_devs=["0"])
  assert out == "gpu_scoped"
  qs.filter.assert_called_once_with(dev__in=["0"])


def test_job_is_shared_node_affinity_true():
  with (
    patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja,
    patch("hpcperfstats.site.lib.machine.models.host_data") as hd,
  ):
    ja.objects.filter.return_value.exists.return_value = True
    assert job_is_shared_node("5") is True
    hd.objects.filter.assert_not_called()


def test_job_is_shared_node_affinity_miss_no_host_data_sql():
  with (
    patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja,
    patch("hpcperfstats.site.lib.machine.models.host_data") as hd,
    patch("hpcperfstats.site.lib.machine.models.job_data") as jd,
  ):
    ja.objects.filter.return_value.exists.return_value = False
    assert job_is_shared_node("5") is False
    hd.objects.filter.assert_not_called()
    jd.objects.filter.assert_not_called()


def test_job_is_shared_node_empty_jid():
  with patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja:
    assert job_is_shared_node("") is False
    assert job_is_shared_node("   ") is False
    ja.objects.filter.assert_not_called()


def test_shared_host_wide_help_footer_copy():
  text = SHARED_HOST_WIDE_HELP_FOOTER.lower()
  assert "shared node" in text or "node-wide" in text
  assert "host-level" in text or "host-wide" in text or "node-wide" in text
  assert (
    "disambiguat" in text or "not job-scoped" in text or "node-wide" in text
  )


def test_is_job_scoped_host_data_type_allowlist():
  assert is_job_scoped_host_data_type("host_cpu") is True
  assert is_job_scoped_host_data_type("nvidia_gpu") is True
  assert is_job_scoped_host_data_type("host_mem_job") is True
  assert is_job_scoped_host_data_type("host_mem") is False
  assert is_job_scoped_host_data_type("host_ib") is False
  assert is_job_scoped_host_data_type("") is False


def test_apply_job_or_host_wide_scope_shared_residual_skips_jid():
  qs = MagicMock()
  out = apply_job_or_host_wide_scope(qs, "7", "host_mem", shared=True)
  assert out is qs
  qs.filter.assert_not_called()


def test_apply_job_or_host_wide_scope_shared_cpu_scopes():
  qs = MagicMock()
  qs.filter.return_value = "scoped"
  out = apply_job_or_host_wide_scope(qs, "7", "host_cpu", shared=True)
  assert out == "scoped"
  qs.filter.assert_called_once()


def test_use_host_wide_residual_scope_and_footer_gate():
  assert use_host_wide_residual_scope("host_mem", shared=True) is True
  assert use_host_wide_residual_scope("host_cpu", shared=True) is False
  assert (
    show_shared_host_wide_help_footer(is_shared_node=True, typ="host_cpu")
    is False
  )
  assert (
    show_shared_host_wide_help_footer(is_shared_node=True, typ="host_mem")
    is True
  )
  assert (
    show_shared_host_wide_help_footer(is_shared_node=False, typ="host_mem")
    is False
  )


def test_host_wide_residual_ht_cache_key_omits_jid():
  key = host_wide_residual_ht_cache_key("h1,h2", "host_mem", "2026-10-22")
  assert "host_wide_residual_ht" in key
  assert "jid" not in key.lower().split("host_wide_residual_ht")[0]
  assert ":7:" not in key
  assert "host_mem" in key
  assert "2026-10-22" in key


def test_co_tenant_q_excludes_other_job_children():
  """Job A's scope Q must not match job B's jid or j:dev alone."""
  qa = job_host_data_scope_q("A")
  qb = job_host_data_scope_q("B")
  a_vals = {c[1] for c in qa.children}
  b_vals = {c[1] for c in qb.children}
  assert "A" in a_vals and "j:A" in a_vals
  assert "B" in b_vals and "j:B" in b_vals
  assert a_vals.isdisjoint(b_vals)


# -- Exclusive-host attribution (horizon-idle-allocation-tagging, D3) --------

_TODAY_COALESCE = (
  "(COALESCE(h.jid, '') = %s OR COALESCE(h.dev, '') = ('j:' || %s))"
)


def test_job_host_data_scope_sql_params_without_exclusive_is_todays_predicate():
  """An empty exclusive set must give byte-identical SQL to the strict scope."""
  sql, params = job_host_data_scope_sql_params("7")
  assert sql == _TODAY_COALESCE
  assert params == ["7", "7"]
  sql_d, _ = job_host_data_scope_sql_params("7", table_alias="d")
  assert sql_d == _TODAY_COALESCE.replace("h.", "d.")
  assert job_host_data_scope_sql_params("7", exclusive_hosts=[])[0] == (
    _TODAY_COALESCE
  )
  plain, _ = job_host_data_scope_sql_params("7", coalesce=False)
  assert plain == job_host_data_scope_sql()


def test_job_host_data_scope_sql_exclusive_hosts_includes_untagged():
  sql, params = job_host_data_scope_sql_params(
    "71526", exclusive_hosts=["c117-002.x", "c105-036.x", "c117-002.x", ""]
  )
  assert sql.startswith(_TODAY_COALESCE[:-1] + " OR (h.host = ANY(%s::text[])")
  # Untagged only: NULL/''/'-' jid and no co-tenant ``j:`` stamp.
  assert "COALESCE(h.jid, '') IN ('', '-')" in sql
  assert "COALESCE(h.dev, '') NOT LIKE 'j:%%'" in sql
  assert sql.count("%s") == len(params) == 3
  assert params == ["71526", "71526", ["c105-036.x", "c117-002.x"]]


def test_job_exclusive_hosts_shared_or_empty_runs_no_query():
  with patch("hpcperfstats.site.lib.machine.models.job_data") as jd:
    assert job_exclusive_hosts("5", ["a"], 1, 2, shared=True) == []
    assert job_exclusive_hosts("", ["a"], 1, 2, shared=False) == []
    assert job_exclusive_hosts("5", [], 1, 2, shared=False) == []
    assert job_exclusive_hosts("5", ["a"], None, 2, shared=False) == []
    jd.objects.filter.assert_not_called()


def _affinity_mock(ja, rows):
  ja.objects.filter.return_value.exclude.return_value.values_list.return_value = rows


def test_exclusive_hosts_excludes_overlapping_job_hosts():
  with (
    patch("hpcperfstats.site.lib.machine.models.job_data") as jd,
    patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja,
  ):
    chain = jd.objects.filter.return_value.exclude.return_value
    chain.values_list.return_value = [["b", "z"], ["c"]]
    _affinity_mock(ja, [])
    out = job_exclusive_hosts("5", ["a", "b", "c", "d"], 10, 20, shared=False)
    assert out == ["a", "d"]
    _args, kwargs = jd.objects.filter.call_args_list[0]
    # Strict overlap: a job that ends exactly at our start (or starts at our
    # end) touches the edge and does not count as a co-tenant.
    assert kwargs == {
      "host_list__overlap": ["a", "b", "c", "d"],
      "start_time__lt": 20,
      "end_time__gt": 10,
    }
    jd.objects.filter.return_value.exclude.assert_called_once_with(jid="5")


def test_exclusive_hosts_live_affinity_blocks_unaccounted_co_tenant():
  """sacct ingest lists only ended jobs: a co-tenant still running has no
  job_data row yet, but listend already wrote its %affinity mark. Such a jid
  blocks the host; an accounted jid blocks only when its window overlaps."""
  from types import SimpleNamespace

  def _jd_filter(**kw):
    if "jid__in" in kw:  # accounting windows of the live co-tenants
      rows = [("past", 1, 9), ("touch", 20, 30), ("over", 15, 25)]
      return SimpleNamespace(
        values_list=lambda *a, **k: [r for r in rows if r[0] in kw["jid__in"]]
      )
    no_rows = SimpleNamespace(values_list=lambda *a, **k: [])
    return SimpleNamespace(exclude=lambda **k: no_rows)

  with (
    patch("hpcperfstats.site.lib.machine.models.job_data") as jd,
    patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja,
  ):
    jd.objects.filter.side_effect = _jd_filter
    _affinity_mock(
      ja,
      [
        ("running", "a.x"),  # no accounting yet -> blocks
        ("past", "b.x"),  # ended before our start -> no block
        ("touch", "c.x"),  # starts exactly at our end -> no block
        ("over", "d.x"),  # overlaps -> blocks
      ],
    )
    out = job_exclusive_hosts(
      "5",
      ["a", "b", "c", "d", "e"],
      10,
      20,
      shared=False,
      to_host_data=lambda h: f"{h}.x",
    )
    assert out == ["b", "c", "e"]
    _args, kwargs = ja.objects.filter.call_args
    assert kwargs == {
      "host__in": ["a.x", "b.x", "c.x", "d.x", "e.x"],
      "reported_at__gte": 10,
    }
    ja.objects.filter.return_value.exclude.assert_called_once_with(jid="5")


def test_exclusive_hosts_normalizes_other_job_host_lists():
  with (
    patch("hpcperfstats.site.lib.machine.models.job_data") as jd,
    patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja,
  ):
    _affinity_mock(ja, [])
    chain = jd.objects.filter.return_value.exclude.return_value
    chain.values_list.return_value = [" B "]
    out = job_exclusive_hosts(
      "5",
      ["a", "b"],
      10,
      20,
      shared=False,
      normalize=lambda raw: (
        [str(h).strip().lower() for h in raw or []]
        if isinstance(raw, list)
        else [str(raw).strip().lower()]
      ),
    )
    assert out == ["a"]


def test_exclusive_hosts_db_error_falls_back_to_strict(caplog):
  from django.db import DatabaseError

  with patch("hpcperfstats.site.lib.machine.models.job_data") as jd:
    jd.objects.filter.side_effect = DatabaseError("statement timeout")
    with caplog.at_level("WARNING"):
      assert job_exclusive_hosts("5", ["a"], 10, 20, shared=False) == []
  assert "strict job scope" in caplog.text


def test_title_named_job_keeps_gpu_scope_unrestricted():
  """A title-named job never writes job_affinity rows (no %affinity mark), so
  it is not shared and its GPU devices stay unrestricted (not an empty list)."""
  with patch("hpcperfstats.site.lib.machine.models.job_affinity") as ja:
    ja.objects.filter.return_value.exists.return_value = False
    ja.objects.filter.return_value.values_list.return_value = []
    assert job_is_shared_node("79249") is False
    assert gpu_devs_for_job("79249") is None


def test_exclusive_hosts_cache_token_is_order_free_and_bounded():
  a = exclusive_hosts_cache_token([f"c{i:03d}.x" for i in range(80)])
  b = exclusive_hosts_cache_token([f"c{i:03d}.x" for i in reversed(range(80))])
  assert a == b and len(a) <= 16
  assert exclusive_hosts_cache_token([]) == "xh-"
  assert exclusive_hosts_cache_token(["a"]) != exclusive_hosts_cache_token(
    ["b"]
  )


def test_scope_sql_single_builder():
  """Drift guard: the bind-param job predicate has one builder. COPY and the
  window probes import it instead of carrying their own literal copy."""
  import inspect

  from hpcperfstats.analysis.metrics.lib import metrics_host_data_sql
  from hpcperfstats.analysis.metrics.lib.columnar import copy_fetch

  for mod in (copy_fetch, metrics_host_data_sql):
    src = inspect.getsource(mod)
    assert "job_host_data_scope_sql_params" in src
    assert "('j:' || %s)" not in src, mod.__name__


def test_exclusive_attribution_bumps_metrics_contract_version():
  """Attribution changes stored metric values, so the contract version moved
  to 4 and the backfill lane recomputes older jobs (D2)."""
  from hpcperfstats.analysis.metrics.lib import metrics

  assert metrics.METRICS_CONTRACT_VERSION >= 4
