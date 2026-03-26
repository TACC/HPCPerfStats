"""Tests for SPA shell rendering behavior in ReactSPAView."""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import RequestFactory, override_settings

from hpcperfstats.site.hpcperfstats_site.views import ReactSPAView


@pytest.mark.django_db
class TestReactSpaView:
  def test_replaces_bokeh_version_token_from_installed_python_package(self, tmp_path):
    """ReactSPAView injects bokeh.__version__ into the SPA shell."""
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    index_path = frontend_dir / "index.html"
    index_path.write_text(
        (
            "<html><head>"
            '<script src="https://cdn.pydata.org/bokeh/release/bokeh-{{ BOKEH_VERSION }}.min.js"></script>'
            "</head><body></body></html>"
        ),
        encoding="utf-8",
    )

    request = RequestFactory().get("/machine/")
    with override_settings(STATICFILES_DIRS=(str(tmp_path),)), patch(
        "hpcperfstats.site.hpcperfstats_site.views.bokeh.__version__", "9.9.9"
    ):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "{{ BOKEH_VERSION }}" not in body
    assert "bokeh-9.9.9.min.js" in body

  def test_leaves_html_unchanged_when_token_is_not_present(self, tmp_path):
    """ReactSPAView serves HTML as-is if no Bokeh version token exists."""
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    index_path = frontend_dir / "index.html"
    index_path.write_text(
        '<html><head><script src="https://cdn.pydata.org/bokeh/release/bokeh-3.0.0.min.js"></script></head></html>',
        encoding="utf-8",
    )

    request = RequestFactory().get("/machine/")
    with override_settings(STATICFILES_DIRS=(str(tmp_path),)), patch(
        "hpcperfstats.site.hpcperfstats_site.views.bokeh.__version__", "8.8.8"
    ):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "bokeh-3.0.0.min.js" in body
    assert "bokeh-8.8.8.min.js" not in body

  def test_returns_503_when_frontend_index_is_missing(self, tmp_path):
    """ReactSPAView returns actionable 503 when build output is absent."""
    request = RequestFactory().get("/machine/")
    with override_settings(STATICFILES_DIRS=(str(tmp_path),)):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 503
    assert "Frontend not built." in response.content.decode("utf-8")

