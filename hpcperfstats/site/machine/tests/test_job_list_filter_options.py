"""Tests for job list filter_options response builder."""
from unittest.mock import MagicMock

import pytest

from hpcperfstats.site.machine.job_list_filter_options import (
    JOB_LIST_FILTER_OPTIONS_MAX,
    _distinct_string_values,
    build_job_list_filter_options,
)

pytestmark = pytest.mark.machine_unit_mock


def test_distinct_string_values_truncates():
    qs = MagicMock()
    qs.exclude.return_value = qs
    qs.values_list.return_value.distinct.return_value.order_by.return_value = [
        f"u{i}" for i in range(JOB_LIST_FILTER_OPTIONS_MAX + 5)
    ]

    values, truncated = _distinct_string_values(qs, "username", cap=5)
    assert truncated is True
    assert values == ["u0", "u1", "u2", "u3", "u4"]


def test_build_job_list_filter_options_facets_per_dimension():
    calls = []

    def fake_builder(request, exclude_header_dimension=None):
        calls.append(exclude_header_dimension)
        qs = MagicMock()
        qs.exclude.return_value = qs
        if exclude_header_dimension == "state":
            qs.values_list.return_value.distinct.return_value = [
                "COMPLETED",
                "CANCELLED by 123",
                "RUNNING",
            ]
        else:
            qs.values_list.return_value.distinct.return_value.order_by.return_value = ["a"]
        return qs, {}, {}, "-end_time"

    request = MagicMock()
    options = build_job_list_filter_options(request, fake_builder)

    assert set(calls) == {"username", "account", "queue", "state", "performance_sort_rank"}
    assert options["usernames"] == ["a"]
    assert options["states"] == ["completed", "canceled"]
    assert options["truncated"]["usernames"] is False
