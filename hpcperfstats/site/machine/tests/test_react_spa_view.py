"""Tests for SPA shell rendering behavior in ReactSPAView."""

import pytest
from django.test import RequestFactory, override_settings

from hpcperfstats.site.hpcperfstats_site.views import ReactSPAView


@pytest.mark.django_db(databases=[])
class TestReactSpaView:
  def test_serves_frontend_index_unchanged(self, tmp_path):
    """ReactSPAView returns the built index.html as stored on disk."""
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    index_path = frontend_dir / "index.html"
    index_path.write_text(
        "<html><head><title>SPA</title></head><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )

    request = RequestFactory().get("/machine/")
    with override_settings(STATICFILES_DIRS=(str(tmp_path),)):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "<title>SPA</title>" in body
    assert "cdn.pydata.org/bokeh" not in body
    assert "cdn.jsdelivr.net" not in body

  def test_returns_503_when_frontend_index_is_missing(self, tmp_path):
    """ReactSPAView returns actionable 503 when build output is absent."""
    request = RequestFactory().get("/machine/")
    with override_settings(STATICFILES_DIRS=(str(tmp_path),)):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 503
    assert "Frontend not built." in response.content.decode("utf-8")
