"""Tests for major terminal job-state grouping in job list filters."""
import pytest
from django.db.models import Q

from hpcperfstats.site.machine.job_list_state_groups import (
    classify_job_state,
    major_state_label,
    major_state_options_from_raw,
    major_state_q,
    parse_major_state_filter_keys,
)

pytestmark = pytest.mark.machine_unit_mock


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("COMPLETED", "completed"),
        ("CANCELLED", "canceled"),
        ("CANCELLED by 123", "canceled"),
        ("cancelled+", "canceled"),
        ("FAILED", "failed"),
        ("OUT_OF_MEMORY", "failed"),
        ("NODE_FAIL", "failed"),
        ("TIMEOUT", "failed"),
        ("PREEMPTED", "preempted"),
        ("RUNNING", None),
        ("PENDING", None),
    ],
)
def test_classify_job_state(raw, expected):
    assert classify_job_state(raw) == expected


def test_major_state_options_from_raw_collapses_cancel_variants():
    options = major_state_options_from_raw(
        ["CANCELLED", "CANCELLED by 123", "COMPLETED", "RUNNING"]
    )
    assert options == ["completed", "canceled"]


def test_parse_major_state_filter_keys():
    assert parse_major_state_filter_keys("canceled,completed,invalid,CANCELED") == [
        "canceled",
        "completed",
    ]


def test_major_state_q_canceled_matches_prefix():
    q = major_state_q(["canceled"])
    assert q == Q(state__istartswith="CANCEL")


def test_major_state_label():
    assert major_state_label("canceled") == "Canceled"
