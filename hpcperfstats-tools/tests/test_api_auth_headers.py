import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.api_auth import apply_api_key_header


def test_apply_api_key_header_uses_x_api_key():
    headers = apply_api_key_header({}, "secret-key")
    assert headers["X-API-Key"] == "secret-key"
    assert "Authorization" not in headers


def test_apply_api_key_header_ignores_empty_key():
    headers = apply_api_key_header({"Accept": "application/json"}, None)
    assert headers == {"Accept": "application/json"}
