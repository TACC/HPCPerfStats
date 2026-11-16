#!/usr/bin/env python3
"""
Emit nginx ``nginx-api-route-allowlist.inc`` from Django API route templates.

Attributes:
  API_PREFIX: Django template prefix that maps to HTTP ``/api/``.
  CONVERTER_RE: Regex matching a Django path converter segment.
  GENERATED_HEADER: Comment written at the top of the include file.
  LOCATION_BODY: Shared proxy/CSP lines inside each allowlisted location.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

API_PREFIX = "api/"
CONVERTER_RE = re.compile(r"<[^>]+>")
GENERATED_HEADER = (
  "# Generated from EXPECTED_ROUTE_TEMPLATES api/* "
  "(urlconf_route_catalog).\n"
  "# Regenerate with write_nginx_api_route_allowlist_include.py; "
  "do not hand-edit.\n"
)
LOCATION_BODY = (
  "    include /etc/nginx/nginx-django-proxy-common.inc;\n"
  "    include /etc/nginx/nginx-csp-no-active.inc;\n"
  "    proxy_pass http://django_server;\n"
)


def _template_to_location_prefix(template: str) -> str:
  """
  Collapse one Django URL template into an nginx ``^~`` prefix path.

  Args:
    template (str): Route template such as ``api/jobs/<str:pk>/``.

  Returns:
    str: Absolute URI prefix ending in ``/`` when the template implies a
      directory, suitable for ``location ^~``.

  Examples:
    >>> _template_to_location_prefix("api/jobs/<str:pk>/")
    '/api/jobs/'
    >>> _template_to_location_prefix("api/schema/")
    '/api/schema/'
  """
  match = CONVERTER_RE.search(template)
  raw = template[: match.start()] if match is not None else template
  if not raw.startswith("/"):
    raw = "/" + raw
  if match is not None and not raw.endswith("/"):
    raw = raw + "/"
  return raw


def location_prefixes_from_api_templates(
  templates: Iterable[str],
) -> list[str]:
  """
  Build a deduplicated, sorted list of nginx ``^~`` prefixes for API routes.

  Shorter prefixes that already cover a longer path are kept alone so
  ``/api/jobs/`` covers nested job endpoints.

  Args:
    templates (Iterable[str]): Django route templates; non-``api/`` entries
      are ignored.

  Returns:
    list[str]: Sorted absolute prefixes for ``location ^~``.

  Examples:
    >>> location_prefixes_from_api_templates(
    ...   ["api/jobs/", "api/jobs/<str:pk>/", "api/schema/"]
    ... )
    ['/api/jobs/', '/api/schema/']
  """
  prefixes: set[str] = set()
  for template in templates:
    if not template.startswith(API_PREFIX):
      continue
    prefixes.add(_template_to_location_prefix(template))
  ordered = sorted(prefixes, key=lambda p: (len(p), p))
  kept: list[str] = []
  for prefix in ordered:
    if any(prefix.startswith(parent) for parent in kept):
      continue
    kept.append(prefix)
  return sorted(kept)


def render_nginx_api_route_allowlist(prefixes: Iterable[str]) -> str:
  """
  Render the nginx include body for the given location prefixes.

  Args:
    prefixes (Iterable[str]): Absolute ``^~`` prefixes (e.g. ``/api/jobs/``).

  Returns:
    str: Full include file text including the generated header.

  Raises:
    ValueError: If ``prefixes`` is empty (fail closed - never emit a blank
      allowlist).

  Examples:
    >>> "location ^~ /api/schema/" in render_nginx_api_route_allowlist(
    ...   ["/api/schema/"]
    ... )
    True
  """
  prefix_list = list(prefixes)
  if not prefix_list:
    raise ValueError("API route allowlist prefixes must not be empty")
  parts = [GENERATED_HEADER, "\n"]
  for prefix in prefix_list:
    parts.append(f"location ^~ {prefix} {{\n")
    parts.append(LOCATION_BODY)
    parts.append("}\n\n")
  return "".join(parts)


def write_nginx_api_route_allowlist_include(
  *,
  templates: Iterable[str],
  out_path: Path,
) -> list[str]:
  """
  Write ``nginx-api-route-allowlist.inc`` from Django API route templates.

  Args:
    templates (Iterable[str]): Full catalog templates (``api/`` filtered).
    out_path (Path): Destination path for the include file.

  Returns:
    list[str]: Prefixes that were written (for tests/callers).

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> write_nginx_api_route_allowlist_include(
    ...   templates=["api/home/"], out_path=d / "a.inc"
    ... )
    ['/api/home/']
  """
  prefixes = location_prefixes_from_api_templates(templates)
  text = render_nginx_api_route_allowlist(prefixes)
  out_path.parent.mkdir(parents=True, exist_ok=True)
  out_path.write_text(text, encoding="utf-8")
  return prefixes


def _default_templates() -> frozenset[str]:
  """
  Load ``EXPECTED_ROUTE_TEMPLATES`` from the in-tree route catalog module.

  Returns:
    frozenset[str]: Canonical Django route templates.

  Raises:
    RuntimeError: If the catalog module cannot be loaded from disk.
    Exception: Propagates any failure from ``exec_module`` after cleanup.

  Examples:
    >>> "api/jobs/" in _default_templates()
    True
  """
  repo_root = Path(__file__).resolve().parents[1]
  catalog = repo_root / "hpcperfstats" / "tests" / "urlconf_route_catalog.py"
  # Import without requiring Django settings for CLI regen.
  import importlib.util

  name = "urlconf_route_catalog_for_nginx"
  spec = importlib.util.spec_from_file_location(name, catalog)
  if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load route catalog: {catalog}")
  mod = importlib.util.module_from_spec(spec)
  # dataclasses needs the module registered before exec_module.
  sys.modules[name] = mod
  try:
    spec.loader.exec_module(mod)
  except Exception:
    sys.modules.pop(name, None)
    raise
  return frozenset(mod.EXPECTED_ROUTE_TEMPLATES)


def main(argv: list[str] | None = None) -> int:
  """
  CLI entry: regenerate the committed nginx API allowlist include.

  Args:
    argv (list[str] | None): Optional argument vector; ``None`` uses
      ``sys.argv[1:]``.

  Returns:
    int: Process exit code (``0`` on success, ``1`` on empty allowlist).

  Examples:
    >>> main(["--help"])  # doctest: +SKIP
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  default_out = (
    Path(__file__).resolve().parent / "nginx-api-route-allowlist.inc"
  )
  parser.add_argument(
    "--out",
    type=Path,
    default=default_out,
    help="Output path for nginx-api-route-allowlist.inc",
  )
  args = parser.parse_args(argv)
  try:
    prefixes = write_nginx_api_route_allowlist_include(
      templates=_default_templates(),
      out_path=args.out,
    )
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(f"wrote {args.out} ({len(prefixes)} locations)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
