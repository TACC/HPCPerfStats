import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.api_client import ApiClient


def _response(status_code=200, location=None, json_data=None, text=""):
  resp = Mock()
  resp.status_code = status_code
  resp.is_redirect = status_code in (301, 302, 303, 307, 308)
  resp.headers = {}
  if location is not None:
    resp.headers["Location"] = location
  resp.ok = 200 <= status_code < 300
  resp.json.return_value = json_data
  resp.text = text
  return resp


def test_api_client_get_json_success():
  session = Mock()
  session.get.return_value = _response(status_code=200, json_data={"ok": True})
  client = ApiClient("https://example.org/api/", api_key="k", session=session)
  result = client.get_json("home/")
  assert result.ok is True
  assert result.data == {"ok": True}


def test_api_client_post_text_blocks_cross_origin_redirect():
  session = Mock()
  session.post.return_value = _response(status_code=302, location="https://evil.example.net/steal", json_data={})
  client = ApiClient("https://example.org/api/", api_key="k", session=session)
  result = client.post_text("sacct/ingest/?date=2026-01-01", "payload")
  assert result.ok is False
  assert "Cross-origin redirect blocked" in (result.error or "")
  assert session.post.call_count == 1


def test_api_client_post_text_follows_http_to_https_same_host_redirect():
  session = Mock()
  first = _response(status_code=302, location="https://example.org/api/sacct/ingest/?date=2026-01-01")
  second = _response(status_code=200, json_data={"inserted": 1})
  session.post.side_effect = [first, second]
  client = ApiClient("http://example.org/api/", api_key="k", session=session)
  result = client.post_text("sacct/ingest/?date=2026-01-01", "payload")
  assert result.ok is True
  assert result.data == {"inserted": 1}
  assert session.post.call_count == 2


def test_api_client_post_text_blocks_https_to_http_downgrade_redirect():
  session = Mock()
  first = _response(status_code=302, location="http://example.org/api/sacct/ingest/?date=2026-01-01")
  session.post.return_value = first
  client = ApiClient("https://example.org/api/", api_key="k", session=session)
  result = client.post_text("sacct/ingest/?date=2026-01-01", "payload")
  assert result.ok is False
  assert "Cross-origin redirect blocked" in (result.error or "")
  assert session.post.call_count == 1

