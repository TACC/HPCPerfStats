"""Tests for SPA shell rendering behavior in ReactSPAView."""

import pytest
from django.test import RequestFactory, override_settings

from hpcperfstats.site.hpcperfstats_site.views import ReactSPAView


@pytest.mark.django_db(databases=[])
class TestReactSpaView:
  def test_prefers_static_root_index_when_both_paths_exist(self, tmp_path):
    """Serve index from STATIC_ROOT so HTML and /static assets stay in sync."""
    static_root_frontend = tmp_path / "static_root" / "frontend"
    static_root_frontend.mkdir(parents=True, exist_ok=True)
    (static_root_frontend / "index.html").write_text(
        "<html><head><title>From Static Root</title></head><body></body></html>",
        encoding="utf-8",
    )
    static_dirs_frontend = tmp_path / "static_dirs" / "frontend"
    static_dirs_frontend.mkdir(parents=True, exist_ok=True)
    (static_dirs_frontend / "index.html").write_text(
        "<html><head><title>From Static Dirs</title></head><body></body></html>",
        encoding="utf-8",
    )

    request = RequestFactory().get("/machine/")
    with override_settings(
        STATIC_ROOT=str(tmp_path / "static_root"),
        STATICFILES_DIRS=(str(tmp_path / "static_dirs"),),
    ):
      response = ReactSPAView.as_view()(request)

    assert response.status_code == 200
    assert "<title>From Static Root</title>" in response.content.decode("utf-8")

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
