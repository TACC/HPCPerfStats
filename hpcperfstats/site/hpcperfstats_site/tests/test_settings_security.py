"""Security-focused unit tests for settings helper behavior."""

import pytest

from hpcperfstats.site.hpcperfstats_site import settings


def test_parse_cors_allowed_origins_uses_env_list(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.example, https://b.example")
    assert settings._parse_cors_allowed_origins() == [
        "https://a.example",
        "https://b.example",
    ]


def test_validate_cors_allowed_origins_rejects_empty_in_production(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(ValueError):
        settings._validate_cors_allowed_origins([])


def test_validate_cors_allowed_origins_rejects_dev_hosts_in_production(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(ValueError):
        settings._validate_cors_allowed_origins(["http://localhost:5173"])


def test_validate_cors_allowed_origins_skips_collectstatic_invocation(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(
        settings.sys,
        "argv",
        [
            "/usr/local/bin/python3",
            "hpcperfstats/site/manage.py",
            "collectstatic",
            "--noinput",
        ],
    )
    settings._validate_cors_allowed_origins([])


def test_validate_cors_allowed_origins_skips_stdin_python_script(monkeypatch):
    """django_startup.sh runs ``python -`` heredoc after collectstatic."""
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(
        settings.sys,
        "argv",
        ["/usr/local/bin/python3", "-"],
    )
    settings._validate_cors_allowed_origins([])
