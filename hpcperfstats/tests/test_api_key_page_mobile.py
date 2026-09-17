from pathlib import Path


def test_api_key_spa_page_and_styles_include_mobile_friendly_patterns():
  """Regression: API key UI lives in the SPA with wrapping styles for long keys."""
  repo_site = Path(__file__).resolve().parent.parent / "site"
  page = (
      repo_site / "frontend" / "src" / "views" / "PageApiKey.tsx"
  ).read_text(encoding="utf-8")

  assert 'id="api-key-value"' in page
  assert "break-all" in page
  assert "[overflow-wrap:anywhere]" in page
  assert "flex-wrap" in page
