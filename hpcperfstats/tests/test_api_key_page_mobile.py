from pathlib import Path


def test_api_key_page_includes_mobile_viewport_and_wrapping():
    """Regression test for server-rendered API key page mobile friendliness."""
    # File layout:
    #   hpcperfstats/tests/test_api_key_page_mobile.py
    #   hpcperfstats/site/hpcperfstats_site/views.py
    views_py = (
        Path(__file__).resolve().parent.parent / "site" / "hpcperfstats_site" / "views.py"
    )
    content = views_py.read_text(encoding="utf-8")

    assert 'name="viewport"' in content
    assert "word-break: break-all" in content
    assert "@media (max-width: 480px)" in content

