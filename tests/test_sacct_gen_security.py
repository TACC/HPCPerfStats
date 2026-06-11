import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.sacct_gen import SACCT_FIELDS, send_to_api


def test_sacct_fields_includes_reqtres_and_alloctres_without_duplicate_reqtres():
    fields = [f.strip() for f in SACCT_FIELDS.split(",")]
    assert fields.count("reqtres") == 1
    assert "alloctres" in fields
    assert len(fields) == len(set(fields))


def test_send_to_api_follows_same_origin_redirect():
    fake_result = Mock(ok=True, data={"inserted": 9}, error=None)
    with patch("hpcperfstats_tools.sacct_gen.ApiClient") as client_cls:
        client_cls.return_value.post_text.return_value = fake_result
        ok, inserted = send_to_api("https://example.org", "secret-key", "2026-03-23", "payload")

    assert ok is True
    assert inserted == 9
    client_cls.return_value.post_text.assert_called_once()


def test_send_to_api_blocks_cross_origin_redirect():
    fake_result = Mock(ok=False, data=None, error="Cross-origin redirect blocked: https://evil.example.net/steal")
    with patch("hpcperfstats_tools.sacct_gen.ApiClient") as client_cls:
        client_cls.return_value.post_text.return_value = fake_result
        ok, message = send_to_api("https://example.org", "secret-key", "2026-03-23", "payload")

    assert ok is False
    assert "Cross-origin redirect blocked" in message


def test_send_to_api_follows_relative_redirect_on_same_origin():
    fake_result = Mock(ok=True, data={"inserted": 2}, error=None)
    with patch("hpcperfstats_tools.sacct_gen.ApiClient") as client_cls:
        client_cls.return_value.post_text.return_value = fake_result
        ok, inserted = send_to_api("https://example.org/api", "secret-key", "2026-03-23", "payload")

    assert ok is True
    assert inserted == 2
