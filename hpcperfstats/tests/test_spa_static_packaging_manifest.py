"""Regression: SPA Vite output must ship in wheels/sdists for Docker collectstatic."""

from pathlib import Path

import tomllib


def test_manifest_in_includes_site_static_tree():
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / "MANIFEST.in"
    text = manifest.read_text(encoding="utf-8")
    assert "recursive-include hpcperfstats/site/hpcperfstats_site/static" in text


def test_pyproject_package_data_includes_site_static_globs():
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pkg_data = data["tool"]["setuptools"]["package-data"]
    site_static = pkg_data.get("hpcperfstats.site.hpcperfstats_site", [])
    assert any("static/" in entry for entry in site_static)
