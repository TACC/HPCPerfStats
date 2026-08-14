"""Host unit tests for job list histogram plotting (no 5k sampling cap)."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.machine_unit_mock


def test_job_list_histograms_uses_full_nj_when_large():
    from hpcperfstats.site.lib.machine.api import job_list_histograms

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/",
        {"group": "metric", "metric": "runtime"},
    )
    request.session = {"username": "admin", "is_staff": True}
    mock_df = pd.DataFrame({"runtime": [1.0]}, index=["1"])
    over_nj = 12000
    plot_qs = MagicMock()

    with patch("hpcperfstats.site.lib.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.lib.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), over_nj, {}, {}),
    ), patch(
        "hpcperfstats.site.lib.machine.api._histogram_queryset_for_plotting",
        return_value=(plot_qs, over_nj, False),
    ) as mock_plot_qs, patch(
        "hpcperfstats.site.lib.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours")], ["1"]),
    ), patch(
        "hpcperfstats.site.lib.machine.api._job_list_metric_hist_pair",
        return_value=(MagicMock(), MagicMock()),
    ), patch(
        "hpcperfstats.site.lib.machine.api._sanitize_hist_plot_item",
        return_value={"doc": {"roots": [{"id": "r1"}]}, "root_id": "r1"},
    ):
        response = job_list_histograms(request)

    assert response.status_code == 200
    data = response.json()
    assert data["nj"] == over_nj
    assert data["histogram_nj"] == over_nj
    assert data["histogram_sampled"] is False
    mock_plot_qs.assert_called_once()


def test_histogram_queryset_for_plotting_returns_full_queryset():
    from hpcperfstats.site.lib.machine.api import _histogram_queryset_for_plotting

    qs = MagicMock()
    plot_qs, histogram_nj, histogram_sampled = _histogram_queryset_for_plotting(qs, 10000)
    assert plot_qs is qs
    assert histogram_nj == 10000
    assert histogram_sampled is False


def test_build_histogram_queryset_orders_by_jid_not_user_order_by():
    """Presentation order_by must not drive histogram plot materialization."""
    from hpcperfstats.site.lib.machine.api import _build_histogram_queryset

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"order_by": "-metrics_distinct_time_count", "end_time__date": "2024-01-15"},
    )
    mock_qs = MagicMock()
    ordered = MagicMock()
    mock_qs.order_by.return_value = ordered
    ordered.count.return_value = 3

    with patch(
        "hpcperfstats.site.lib.machine.api._build_job_list_queryset_from_request",
        return_value=(mock_qs, {"order_by": "-metrics_distinct_time_count"}, {}, "-metrics_distinct_time_count"),
    ) as mock_build:
        job_list_qs, nj, _fields, _cur = _build_histogram_queryset(request)

    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs.get("ignore_order_by") is True
    assert mock_build.call_args.kwargs.get("annotate_all") is True
    mock_qs.order_by.assert_called_with("jid")
    assert job_list_qs is ordered
    assert nj == 3


def test_build_job_list_queryset_ignore_order_by_skips_user_sort():
    """Histogram path must not apply expensive user order_by before forced jid."""
    from hpcperfstats.site.lib.machine.api import _build_job_list_queryset_from_request

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"order_by": "-runtime", "end_time__date": "2024-01-15"},
    )
    mock_qs = MagicMock()
    mock_qs.filter.return_value = mock_qs

    with patch(
        "hpcperfstats.site.lib.machine.api.job_data.objects.filter",
        return_value=mock_qs,
    ), patch(
        "hpcperfstats.site.lib.machine.api._apply_non_staff_job_visibility",
        side_effect=lambda qs, _req: qs,
    ), patch(
        "hpcperfstats.site.lib.machine.api.annotate_job_list_performance_fields",
        side_effect=lambda qs: qs,
    ), patch(
        "hpcperfstats.site.lib.machine.api._apply_job_list_performance_sort_rank_filter",
        side_effect=lambda qs, _fields: qs,
    ), patch(
        "hpcperfstats.site.lib.machine.api._apply_job_list_major_state_filter",
        side_effect=lambda qs, _fields: qs,
    ), patch(
        "hpcperfstats.site.lib.machine.api._apply_job_list_metric_filters",
        side_effect=lambda qs, _metrics: qs,
    ):
        _qs, _fields, _cur, order_by = _build_job_list_queryset_from_request(
            request,
            annotate_all=True,
            ignore_order_by=True,
        )

    assert order_by == "-runtime"
    mock_qs.order_by.assert_not_called()


def test_job_list_histograms_batch_uses_full_nj_when_large():
    from hpcperfstats.site.lib.machine.api import job_list_histograms_batch

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/histograms/batch/",
        {"metrics": "runtime,nhosts"},
    )
    request.session = {"username": "admin", "is_staff": True}
    over_nj = 7500

    mock_df = MagicMock()
    with patch("hpcperfstats.site.lib.machine.api.check_for_tokens", return_value=True), patch(
        "hpcperfstats.site.lib.machine.api._build_histogram_queryset",
        return_value=(MagicMock(), over_nj, {}, {}),
    ), patch(
        "hpcperfstats.site.lib.machine.api._histogram_queryset_for_plotting",
        return_value=(MagicMock(), over_nj, False),
    ), patch(
        "hpcperfstats.site.lib.machine.api._build_histogram_dataframe",
        return_value=(mock_df, [("runtime", "hours"), ("nhosts", "# nodes")], ["j1"]),
    ), patch(
        "hpcperfstats.site.lib.machine.api._build_metric_histogram_payload",
        side_effect=[
            {"metric": "runtime", "nj": over_nj},
            {"metric": "nhosts", "nj": over_nj},
        ],
    ):
        response = job_list_histograms_batch(request)

    assert response.status_code == 200
    data = response.json()
    assert data["nj"] == over_nj
    assert data["histogram_nj"] == over_nj
    assert data["histogram_sampled"] is False
    assert len(data["histograms"]) == 2
