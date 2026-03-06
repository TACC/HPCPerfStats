"""Unit tests for progress bar module.

AI generated.
"""
import io
import sys

import pytest


def test_progress_bar_completion(capsys):
    """progress(100, 100) produces a full bar.

    AI generated.
    """
    from hpcperfstats.progress import progress
    progress(100, 100, status="done")
    captured = capsys.readouterr()
    assert "100.0%" in captured.out or "100%" in captured.out
    assert "=" in captured.out


def test_progress_bar_half(capsys):
    """progress(50, 100) produces half bar.

    AI generated.
    """
    from hpcperfstats.progress import progress
    progress(50, 100, status="")
    captured = capsys.readouterr()
    assert "50" in captured.out


def test_progress_bar_zero_total(capsys):
    """progress with total=0 does not raise (avoids ZeroDivisionError).

    AI generated.
    """
    from hpcperfstats.progress import progress
    progress(0, 0, status="")
    captured = capsys.readouterr()
    assert "0" in captured.out or "]" in captured.out
