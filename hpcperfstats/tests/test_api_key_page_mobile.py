from pathlib import Path


def test_api_key_spa_page_and_styles_include_mobile_friendly_patterns():
  """Regression: API key UI lives in the SPA with wrapping styles for long keys."""
  repo_site = Path(__file__).resolve().parent.parent / "site"
  page = (repo_site / "frontend" / "src" / "pages" / "PageApiKey.jsx").read_text(encoding="utf-8")
  css = (repo_site / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
  index_html = (repo_site / "frontend" / "index.html").read_text(encoding="utf-8")

  assert 'name="viewport"' in index_html
  assert "api-key-code-block" in page
  assert "word-break" in css and "break-all" in css
  assert "page-api-key-container" in css
  assert "@media (max-width: 480px)" in css
