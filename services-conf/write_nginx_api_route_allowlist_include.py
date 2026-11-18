#!/usr/bin/env python3
"""
Emit nginx ``nginx-api-route-allowlist.inc`` from Django API route templates.

Attributes:
  API_PREFIX: Django template prefix that maps to HTTP ``/api/``.
  CONVERTER_RE: Regex matching a Django path converter segment.
  CONVERTER_PATTERNS: PCRE fragment per Django path converter.
  _PCRE_META: PCRE metacharacters escaped in literal route pieces.
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
CONVERTER_RE = re.compile(
  r"<(?:(?P<conv>[a-z]+):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>"
)
# Django path converter -> PCRE fragment for one decoded, normalized $uri piece.
CONVERTER_PATTERNS = {
  "str": "[^/]+",
  "int": "[0-9]+",
  "slug": "[-a-zA-Z0-9_]+",
  "uuid": "[0-9a-fA-F-]+",
  "path": ".+",
}
GENERATED_HEADER = (
  "# Generated from EXPECTED_ROUTE_TEMPLATES api/* "
  "(urlconf_route_catalog).\n"
  "# Regenerate with write_nginx_api_route_allowlist_include.py; "
  "do not hand-edit.\n"
  "# Exact (=) or anchored regex (~) per route: scanner probes under a real\n"
  "# route prefix (/api/session/new) 404 here instead of reaching Django.\n"
)
LOCATION_BODY = (
  "    include /etc/nginx/nginx-django-proxy-common.inc;\n"
  "    include /etc/nginx/nginx-csp-no-active.inc;\n"
  "    proxy_pass http://django_server;\n"
)
_PCRE_META = frozenset("\\.^$|?*+()[]{}")


def _escape_literal(text: str) -> str:
  """
  Escape PCRE metacharacters in a literal URL piece (``/`` and ``-`` kept).

  Args:
    text (str): Literal part of a Django route template.

  Returns:
    str: Text safe to embed in an nginx ``location ~`` regex.

  Examples:
    >>> _escape_literal("api/x.json")
    'api/x\\\\.json'
  """
  return "".join("\\" + ch if ch in _PCRE_META else ch for ch in text)


def _template_to_location_head(template: str) -> str:
  """
  Map one Django URL template to the nginx ``location`` modifier and URI.

  Templates without converters become exact matches. Templates with
  converters become an anchored, case-sensitive regex so only the shapes
  Django resolves reach Gunicorn.

  Args:
    template (str): Route template such as ``api/jobs/<str:pk>/``.

  Returns:
    str: ``= /api/...`` or ``~ ^/api/...$`` (no ``location`` keyword).

  Raises:
    ValueError: If the template uses a converter without a known pattern.

  Examples:
    >>> _template_to_location_head("api/schema/")
    '= /api/schema/'
    >>> _template_to_location_head("api/jobs/<str:pk>/")
    '~ ^/api/jobs/[^/]+/$'
  """
  if CONVERTER_RE.search(template) is None:
    return "= /" + template.lstrip("/")
  parts: list[str] = []
  pos = 0
  for match in CONVERTER_RE.finditer(template):
    parts.append(_escape_literal(template[pos : match.start()]))
    conv = match.group("conv") or "str"
    if conv not in CONVERTER_PATTERNS:
      raise ValueError(
        f"no nginx pattern for converter {conv!r} in {template!r}"
      )
    parts.append(CONVERTER_PATTERNS[conv])
    pos = match.end()
  parts.append(_escape_literal(template[pos:]))
  return "~ ^/" + "".join(parts).lstrip("/") + "$"


def location_heads_from_api_templates(
  templates: Iterable[str],
) -> list[str]:
  """
  Build the sorted, deduplicated nginx ``location`` heads for API routes.

  Exact matches come first, then regex locations, so the include reads in
  the same order nginx evaluates them.

  Args:
    templates (Iterable[str]): Django route templates; non-``api/`` entries
      are ignored.

  Returns:
    list[str]: Location heads such as ``= /api/jobs/``.

  Examples:
    >>> location_heads_from_api_templates(
    ...   ["api/jobs/", "api/jobs/<str:pk>/", "api/schema/", "login/"]
    ... )
    ['= /api/jobs/', '= /api/schema/', '~ ^/api/jobs/[^/]+/$']
  """
  heads = {
    _template_to_location_head(t) for t in templates if t.startswith(API_PREFIX)
  }
  return sorted(heads, key=lambda h: (not h.startswith("= "), h))


def render_nginx_api_route_allowlist(heads: Iterable[str]) -> str:
  """
  Render the nginx include body for the given location heads.

  Args:
    heads (Iterable[str]): Heads from ``location_heads_from_api_templates``.

  Returns:
    str: Full include file text including the generated header.

  Raises:
    ValueError: If ``heads`` is empty (fail closed - never emit a blank
      allowlist).

  Examples:
    >>> "location = /api/schema/ {" in render_nginx_api_route_allowlist(
    ...   ["= /api/schema/"]
    ... )
    True
  """
  head_list = list(heads)
  if not head_list:
    raise ValueError("API route allowlist must not be empty")
  parts = [GENERATED_HEADER, "\n"]
  for head in head_list:
    parts.append(f"location {head} {{\n")
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
    list[str]: Location heads that were written (for tests/callers).

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> write_nginx_api_route_allowlist_include(
    ...   templates=["api/home/"], out_path=d / "a.inc"
    ... )
    ['= /api/home/']
  """
  heads = location_heads_from_api_templates(templates)
  text = render_nginx_api_route_allowlist(heads)
  out_path.parent.mkdir(parents=True, exist_ok=True)
  out_path.write_text(text, encoding="utf-8")
  return heads


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
    heads = write_nginx_api_route_allowlist_include(
      templates=_default_templates(),
      out_path=args.out,
    )
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(f"wrote {args.out} ({len(heads)} locations)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
