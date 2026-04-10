"""Regression: Bokeh job-list figures must never emit degenerate y-ranges.

BokehJS 3.9 logs ``could not set initial ranges`` and may cascade to
``FigureView wasn't built properly`` / ``is_valid`` layout errors when Range1d
has zero span (start == end).
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

pytestmark = pytest.mark.django_db(databases=[])


def _assert_positive_y_span(plot):
    yr = plot.y_range
    start = float(yr.start)
    end = float(yr.end)
    assert end > start, f"degenerate y_range: ({start}, {end})"


def test_job_hist_empty_bins_do_not_use_inverted_quads():
    """Bins with count 0 must not use top < bottom (Bokeh 3.9 blank embed)."""
    from hpcperfstats.site.machine.views import job_hist

    df = pd.DataFrame({"runtime": [0.0, 0.0, 0.0, 100.0, 100.0, 100.0]})
    plot = job_hist(df, "runtime", "hours", width=280, height=200)
    assert plot is not None
    tops = plot.renderers[0].data_source.data["top"]
    assert min(float(x) for x in tops) == 0.0
    assert max(float(x) for x in tops) >= 1.0
    assert float(plot.renderers[0].glyph.bottom) == 0.0


def test_job_hist_y_range_strictly_positive_when_max_bin_count_is_one():
    """Single finite value → max histogram count 1; y_range must not be (1, 1)."""
    from hpcperfstats.site.machine.views import job_hist

    df = pd.DataFrame({"runtime": [42.0]})
    plot = job_hist(df, "runtime", "hours", width=280, height=200)
    assert plot is not None
    _assert_positive_y_span(plot)


def test_job_hist_y_range_strictly_positive_when_max_bin_count_equals_y_floor():
    """Several values still yielding max count 1 must not produce zero-span range."""
    from hpcperfstats.site.machine.views import job_hist

    # Wide spread so many bins; each value alone in a bin → max(hist) == 1
    df = pd.DataFrame({"runtime": [1.0, 100.0, 1000.0, 10000.0]})
    plot = job_hist(df, "runtime", "hours", width=280, height=200)
    assert plot is not None
    _assert_positive_y_span(plot)


def test_job_list_queue_bar_chart_y_range_strictly_positive_when_all_tops_zero():
    """All-zero vbar tops must still get a positive y span (Bokeh 3.9 embed)."""
    from hpcperfstats.site.machine.api import _job_list_queue_bar_chart

    mock_qs = MagicMock()
    values_chain = mock_qs.values.return_value
    annotate_chain = values_chain.annotate.return_value
    order_chain = annotate_chain.order_by.return_value
    order_chain.values_list.return_value = [("batch", 0), ("debug", 0)]

    plot = _job_list_queue_bar_chart(mock_qs, width=280, height=200, metric="jobs")
    assert plot is not None
    _assert_positive_y_span(plot)


def test_job_list_queue_bar_chart_y_range_strictly_positive_when_max_top_one():
    """Single-job queues (top values 1) must not collapse y_range."""
    from hpcperfstats.site.machine.api import _job_list_queue_bar_chart

    mock_qs = MagicMock()
    values_chain = mock_qs.values.return_value
    annotate_chain = values_chain.annotate.return_value
    order_chain = annotate_chain.order_by.return_value
    order_chain.values_list.return_value = [("q1", 1), ("q2", 1)]

    plot = _job_list_queue_bar_chart(mock_qs, width=280, height=200, metric="jobs")
    assert plot is not None
    _assert_positive_y_span(plot)


def test_job_list_queue_bar_chart_node_hours_all_zero():
    from hpcperfstats.site.machine.api import _job_list_queue_bar_chart

    mock_qs = MagicMock()
    values_chain = mock_qs.values.return_value
    annotate_chain = values_chain.annotate.return_value
    order_chain = annotate_chain.order_by.return_value
    order_chain.values_list.return_value = [("q1", 0), ("q2", 0)]

    plot = _job_list_queue_bar_chart(mock_qs, width=280, height=200, metric="node_hours")
    assert plot is not None
    _assert_positive_y_span(plot)


def test_job_list_queue_bar_chart_merges_null_and_empty_queue_for_unique_factors():
    """NULL vs '' are separate SQL groups but both label as '(no queue)'; Bokeh forbids duplicate factors."""
    from hpcperfstats.site.machine.api import _job_list_queue_bar_chart

    mock_qs = MagicMock()
    values_chain = mock_qs.values.return_value
    annotate_chain = values_chain.annotate.return_value
    order_chain = annotate_chain.order_by.return_value
    order_chain.values_list.return_value = [(None, 2), ("", 3), ("normal", 5)]

    plot = _job_list_queue_bar_chart(mock_qs, width=280, height=200, metric="jobs")
    assert plot is not None
    factors = list(plot.x_range.factors)
    assert len(factors) == len(set(factors))
    data = plot.renderers[0].data_source.data
    by_x = dict(zip(data["x"], data["top"]))
    assert by_x["(no queue)"] == 5
    assert by_x["normal"] == 5


def test_job_list_queue_bar_chart_merges_whitespace_queue_labels_for_node_hours():
    from hpcperfstats.site.machine.api import _job_list_queue_bar_chart

    mock_qs = MagicMock()
    values_chain = mock_qs.values.return_value
    annotate_chain = values_chain.annotate.return_value
    order_chain = annotate_chain.order_by.return_value
    order_chain.values_list.return_value = [
        (None, 1.0),
        ("  ", 2.0),
        ("batch", 10.0),
    ]

    plot = _job_list_queue_bar_chart(mock_qs, width=280, height=200, metric="node_hours")
    assert plot is not None
    factors = list(plot.x_range.factors)
    assert len(factors) == len(set(factors))
    data = plot.renderers[0].data_source.data
    by_x = dict(zip(data["x"], data["top"]))
    assert by_x["(no queue)"] == 3.0
    assert by_x["batch"] == 10.0
