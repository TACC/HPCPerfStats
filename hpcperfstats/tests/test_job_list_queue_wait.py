"""Queue wait contract (pandas) for job list / histogram alignment — runs without Compose DB."""

import pandas as pd
import pytest

from hpcperfstats.site.lib.machine.job_list_queue_wait import queue_wait_hours_series


@pytest.mark.parametrize(
    ("submit", "start", "want_hours"),
    (
        ("2024-01-01 12:00:00+00:00", "2024-01-01 14:00:00+00:00", 2.0),
        ("2024-01-01 12:00:00+00:00", "2024-01-01 12:30:00+00:00", 0.5),
    ),
)
def test_queue_wait_hours_series_positive_waits(submit, start, want_hours):
    s_submit = pd.Series(pd.to_datetime([submit]))
    s_start = pd.Series(pd.to_datetime([start]))
    got = queue_wait_hours_series(s_start, s_submit)
    assert abs(float(got.iloc[0]) - want_hours) < 1e-9


def test_queue_wait_hours_series_nan_when_start_before_submit():
    submit = pd.Series(pd.to_datetime(["2024-01-01 14:00:00+00:00"]))
    start = pd.Series(pd.to_datetime(["2024-01-01 12:00:00+00:00"]))
    got = queue_wait_hours_series(start, submit)
    assert pd.isna(got.iloc[0])
