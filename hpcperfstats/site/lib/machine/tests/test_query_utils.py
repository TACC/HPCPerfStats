"""Unit tests for query_utils date normalization and month expansion."""

import pytest
from django.utils import timezone as dj_tz

from hpcperfstats.site.lib.machine.query_utils import (
    coerce_job_list_datetime_bounds,
    expand_month_date_to_range,
    get_job_list_order_by,
    normalize_date_param,
    normalize_job_list_query_params,
    partition_job_list_acct_filters,
)


class TestNormalizeDateParam:
    """normalize_date_param converts shorthand to YYYY-MM-DD."""

    def test_shorthand_month_to_first_day(self):
        assert normalize_date_param("2026-1") == "2026-01-01"
        assert normalize_date_param("2026-12") == "2026-12-01"

    def test_shorthand_month_day(self):
        assert normalize_date_param("2026-1-5") == "2026-01-05"
        assert normalize_date_param("2026-12-3") == "2026-12-03"

    def test_full_date_unchanged(self):
        assert normalize_date_param("2026-01-05") == "2026-01-05"
        assert normalize_date_param("2026-01-05T00:00:00") == "2026-01-05"

    def test_non_date_unchanged(self):
        assert normalize_date_param("queue_name") == "queue_name"
        assert normalize_date_param("") == ""
        assert normalize_date_param(None) is None


class TestNormalizeJobListQueryParams:
    """normalize_job_list_query_params only normalizes time-related keys."""

    def test_normalizes_time_params(self):
        out = normalize_job_list_query_params({"end_time__date": "2026-1"})
        assert out["end_time__date"] == "2026-01-01"

    def test_preserves_month_only_end_time_date(self):
        """Month-only YYYY-MM is preserved so expand_month_date_to_range can expand to full month."""
        out = normalize_job_list_query_params({"end_time__date": "2026-01"})
        assert out["end_time__date"] == "2026-01"
        # Full pipeline: normalize (preserve) then expand
        expanded = expand_month_date_to_range(out)
        assert expanded["end_time__date__gte"] == "2026-01-01"
        assert expanded["end_time__date__lte"] == "2026-01-31"

    def test_preserves_year_only_end_time_date(self):
        """Year-only YYYY is preserved so expand_month_date_to_range can expand to full year."""
        out = normalize_job_list_query_params({"end_time__date": "2024"})
        assert out["end_time__date"] == "2024"
        expanded = expand_month_date_to_range(out)
        assert expanded["end_time__date__gte"] == "2024-01-01"
        assert expanded["end_time__date__lte"] == "2024-12-31"

    def test_leaves_other_params_unchanged(self):
        out = normalize_job_list_query_params({"queue": "normal", "page": "1"})
        assert out["queue"] == "normal"
        assert out["page"] == "1"


class TestGetJobListOrderBy:
    """get_job_list_order_by maps allowed sort fields; unknown values return None."""

    def test_performance_sort_rank_asc_and_desc(self):
        assert get_job_list_order_by({"order_by": "performance_sort_rank"}) == "performance_sort_rank"
        assert get_job_list_order_by({"order_by": "-performance_sort_rank"}) == "-performance_sort_rank"

    def test_legacy_has_metrics_not_allowed(self):
        assert get_job_list_order_by({"order_by": "has_metrics"}) is None
        assert get_job_list_order_by({"order_by": "-has_metrics"}) is None

    def test_sample_count_maps_to_metrics_distinct_time_count(self):
        assert get_job_list_order_by({"order_by": "sample_count"}) == "metrics_distinct_time_count"
        assert get_job_list_order_by({"order_by": "-sample_count"}) == (
            "-metrics_distinct_time_count"
        )


class TestPartitionJobListAcctFilters:
    """partition_job_list_acct_filters drops unknown keys and extracts host."""

    def test_drops_unknown_and_keeps_allowed(self):
        allowed, host = partition_job_list_acct_filters(
            {
                "end_time__date": "2026-04-28",
                "username": "alice",
                "bogus_param": "x",
                "host": "n1.example.com",
            },
        )
        assert host == "n1.example.com"
        assert allowed == {"end_time__date": "2026-04-28", "username": "alice"}

    def test_host_only(self):
        allowed, host = partition_job_list_acct_filters({"host": "  h1  "})
        assert allowed == {}
        assert host == "h1"


class TestExpandMonthDateToRange:
    """expand_month_date_to_range expands YYYY-MM to gte/lte for that month."""

    def test_expands_month_only(self):
        out = expand_month_date_to_range({"end_time__date": "2026-01"})
        assert "end_time__date" not in out
        assert out["end_time__date__gte"] == "2026-01-01"
        assert out["end_time__date__lte"] == "2026-01-31"

    def test_expands_february(self):
        out = expand_month_date_to_range({"end_time__date": "2024-02"})
        assert out["end_time__date__lte"] == "2024-02-29"

    def test_full_date_not_expanded(self):
        out = expand_month_date_to_range({"end_time__date": "2026-01-15"})
        assert out["end_time__date"] == "2026-01-15"
        assert "end_time__date__gte" not in out

    def test_expands_year_only(self):
        """Year-only YYYY is expanded to full year range."""
        out = expand_month_date_to_range({"end_time__date": "2024"})
        assert "end_time__date" not in out
        assert out["end_time__date__gte"] == "2024-01-01"
        assert out["end_time__date__lte"] == "2024-12-31"

    def test_no_end_time_date_unchanged(self):
        out = expand_month_date_to_range({"queue": "x"})
        assert out == {"queue": "x"}


@pytest.mark.machine_unit_mock
class TestCoerceJobListDatetimeBounds:
    """Date-only end_time__gte/lte become aware local-day bounds (USE_TZ safe)."""

    def test_date_only_gte_start_of_day_lte_end_of_day(self):
        out = coerce_job_list_datetime_bounds(
            {
                "end_time__gte": "2026-07-25",
                "end_time__lte": "2026-07-26",
                "queue": "h100",
            },
        )
        assert out["queue"] == "h100"
        gte = out["end_time__gte"]
        lte = out["end_time__lte"]
        assert dj_tz.is_aware(gte)
        assert dj_tz.is_aware(lte)
        assert gte.hour == 0 and gte.minute == 0 and gte.second == 0
        assert gte.date().isoformat() == "2026-07-25"
        assert lte.date().isoformat() == "2026-07-26"
        assert lte.hour == 23 and lte.minute == 59
        assert lte > gte

    def test_aware_iso_unchanged(self):
        raw = "2026-07-25T12:30:00+00:00"
        out = coerce_job_list_datetime_bounds({"end_time__gte": raw})
        assert out["end_time__gte"] == raw

    def test_naive_midnight_string_becomes_aware(self):
        out = coerce_job_list_datetime_bounds(
            {"end_time__lte": "2026-07-26T00:00:00"},
        )
        lte = out["end_time__lte"]
        assert dj_tz.is_aware(lte)
        assert lte.date().isoformat() == "2026-07-26"
        # Midnight-only ISO without offset is treated as date-only end bound.
        assert lte.hour == 23
