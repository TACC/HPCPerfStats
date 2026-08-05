#!/usr/bin/env python3
"""
SPA CSP helpers: hash inline scripts/styles and embed per-document CSP meta.

Used by proxy startup (private ``/etc/nginx`` includes) and by Django SPA heal
(document-embedded meta so HTML and policy cannot desync).

Attributes:
  INLINE_SCRIPT_RE: Regex matching inline ``<script>`` bodies (no ``src``).
  INLINE_STYLE_RE: Regex matching ``<style>`` bodies.
  STYLE_ATTR_RE: Regex matching HTML ``style="…"`` attribute values.
  CSP_META_RE: Regex matching an existing CSP ``<meta http-equiv>`` tag.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

INLINE_SCRIPT_RE = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)</script>",
    re.IGNORECASE,
)
INLINE_STYLE_RE = re.compile(r"<style\b[^>]*>([\s\S]*?)</style>", re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r"\sstyle\s*=\s*(['\"])([\s\S]*?)\1", re.IGNORECASE)
CSP_META_RE = re.compile(
    r"<meta\s+http-equiv=(['\"])Content-Security-Policy\1[^>]*>\s*",
    re.IGNORECASE,
)


def sha256_csp_hash(content: str) -> str:
  """
  Return a CSP ``'sha256-…'`` token for UTF-8 script/style body bytes.

  Args:
    content (str): Exact inline script or style text as served in HTML.

  Returns:
    str: Quoted CSP hash source (for example ``'sha256-…='``).

  Examples:
    >>> h = sha256_csp_hash("void 0")
    >>> h.startswith("'sha256-") and h.endswith("'")
    True
  """
  digest = base64.b64encode(hashlib.sha256(content.encode("utf-8")).digest()).decode(
      "ascii"
  )
  return f"'sha256-{digest}'"


def extract_inline_csp_hashes_from_html(html: str) -> dict[str, list[str]]:
  """
  Collect sorted unique CSP hashes for inline scripts, styles, and style attrs.

  Args:
    html (str): HTML document text.

  Returns:
    dict[str, list[str]]: Keys ``script_hashes``, ``style_hashes``,
    ``style_attr_hashes``.

  Examples:
    >>> extract_inline_csp_hashes_from_html("<script>x</script>")["script_hashes"][0].startswith("'sha256-")
    True
  """
  script_hashes = {sha256_csp_hash(m.group(1)) for m in INLINE_SCRIPT_RE.finditer(html)}
  style_hashes = {sha256_csp_hash(m.group(1)) for m in INLINE_STYLE_RE.finditer(html)}
  style_attr_hashes = {
      sha256_csp_hash(m.group(2)) for m in STYLE_ATTR_RE.finditer(html)
  }
  return {
      "script_hashes": sorted(script_hashes),
      "style_hashes": sorted(style_hashes),
      "style_attr_hashes": sorted(style_attr_hashes),
  }


def list_html_files(root_dir: Path) -> list[Path]:
  """
  Return sorted ``*.html`` paths under ``root_dir`` (recursive).

  Args:
    root_dir (Path): Directory to walk.

  Returns:
    list[Path]: HTML file paths, sorted.

  Examples:
    >>> list_html_files(Path("/no/such/dir"))
    []
  """
  if not root_dir.is_dir():
    return []
  return sorted(p for p in root_dir.rglob("*.html") if p.is_file())


def collect_inline_csp_hashes(root_dir: Path) -> dict[str, list[str]]:
  """
  Union inline CSP hashes from every HTML file under ``root_dir``.

  Args:
    root_dir (Path): SPA segment directory (for example ``…/frontend/machine``).

  Returns:
    dict[str, list[str]]: Merged sorted hash lists.

  Examples:
    >>> collect_inline_csp_hashes(Path("/no/such/dir"))["script_hashes"]
    []
  """
  script_hashes: set[str] = set()
  style_hashes: set[str] = set()
  style_attr_hashes: set[str] = set()
  for path in list_html_files(root_dir):
    text = CSP_META_RE.sub("", path.read_text(encoding="utf-8"))
    extracted = extract_inline_csp_hashes_from_html(text)
    script_hashes.update(extracted["script_hashes"])
    style_hashes.update(extracted["style_hashes"])
    style_attr_hashes.update(extracted["style_attr_hashes"])
  return {
      "script_hashes": sorted(script_hashes),
      "style_hashes": sorted(style_hashes),
      "style_attr_hashes": sorted(style_attr_hashes),
  }


def build_csp_policy(
    *,
    script_hashes: list[str] | None = None,
    style_hashes: list[str] | None = None,
    style_attr_hashes: list[str] | None = None,
    allow_unsafe_eval: bool = False,
    allow_bokeh_style_inline: bool = False,
) -> str:
  """
  Build a Content-Security-Policy header/meta value (no nginx wrapper).

  Args:
    script_hashes (list[str] | None): Quoted ``'sha256-…'`` tokens for scripts.
    style_hashes (list[str] | None): Quoted hashes for ``<style>`` bodies.
    style_attr_hashes (list[str] | None): Quoted hashes for ``style="…"`` attrs.
    allow_unsafe_eval (bool): When True, append ``'unsafe-eval'`` (machine/Bokeh).
    allow_bokeh_style_inline (bool): When True, use ``style-src 'self'
      'unsafe-inline'`` and omit style hashes (CSP3 ignores ``unsafe-inline``
      when hashes are present). Required for BokehJS runtime ``<style>`` tags.

  Returns:
    str: CSP policy string.

  Examples:
    >>> "unsafe-eval" in build_csp_policy(allow_unsafe_eval=True)
    True
    >>> "unsafe-inline" in build_csp_policy(allow_bokeh_style_inline=True)
    True
  """
  scripts = ["'self'", *(script_hashes or [])]
  if allow_unsafe_eval:
    scripts.append("'unsafe-eval'")
  if allow_bokeh_style_inline:
    # CSP3: style hashes disable 'unsafe-inline'; omit them for Bokeh embeds.
    styles = ["'self'", "'unsafe-inline'"]
  else:
    styles = ["'self'", *(style_hashes or [])]
    if style_attr_hashes:
      styles.extend(["'unsafe-hashes'", *style_attr_hashes])
  return "; ".join(
      [
          "default-src 'self'",
          "base-uri 'self'",
          "object-src 'none'",
          "frame-ancestors 'self'",
          "form-action 'self'",
          "img-src 'self' data:",
          "font-src 'self' data:",
          f"style-src {' '.join(styles)}",
          f"script-src {' '.join(scripts)}",
          "connect-src 'self'",
          "upgrade-insecure-requests",
          "report-uri /csp-report/",
      ]
  )


def build_nginx_csp_include(
    *,
    script_hashes: list[str] | None = None,
    style_hashes: list[str] | None = None,
    style_attr_hashes: list[str] | None = None,
    allow_unsafe_eval: bool = False,
    allow_bokeh_style_inline: bool = False,
) -> str:
  """
  Render an nginx ``add_header Content-Security-Policy …`` include body.

  Args:
    script_hashes (list[str] | None): Quoted ``'sha256-…'`` tokens for scripts.
    style_hashes (list[str] | None): Quoted hashes for ``<style>`` bodies.
    style_attr_hashes (list[str] | None): Quoted hashes for ``style="…"`` attrs.
    allow_unsafe_eval (bool): When True, append ``'unsafe-eval'`` (machine/Bokeh).
    allow_bokeh_style_inline (bool): When True, Bokeh-safe ``style-src`` without
      style hashes (see ``build_csp_policy``).

  Returns:
    str: Include file text ending with a newline.

  Examples:
    >>> "add_header Content-Security-Policy" in build_nginx_csp_include()
    True
  """
  policy = build_csp_policy(
      script_hashes=script_hashes,
      style_hashes=style_hashes,
      style_attr_hashes=style_attr_hashes,
      allow_unsafe_eval=allow_unsafe_eval,
      allow_bokeh_style_inline=allow_bokeh_style_inline,
  )
  return f'add_header Content-Security-Policy "{policy}" always;\n'


def inject_csp_meta_into_html(html: str, policy: str) -> str:
  """
  Insert or replace a document CSP ``<meta http-equiv>`` tag.

  Args:
    html (str): HTML document text.
    policy (str): CSP policy value (not an nginx ``add_header`` line).

  Returns:
    str: HTML with a single CSP meta tag in ``<head>`` (or prepended).

  Examples:
    >>> "Content-Security-Policy" in inject_csp_meta_into_html(
    ...     "<html><head></head><body></body></html>", "default-src 'self'"
    ... )
    True
  """
  cleaned = CSP_META_RE.sub("", html)
  attr = policy.replace("&", "&amp;").replace('"', "&quot;")
  meta = f'<meta http-equiv="Content-Security-Policy" content="{attr}">'
  head_match = re.search(r"<head([^>]*)>", cleaned, flags=re.IGNORECASE)
  if head_match:
    insert_at = head_match.end()
    return cleaned[:insert_at] + meta + cleaned[insert_at:]
  return meta + cleaned


def inject_csp_meta_into_frontend_tree(frontend_root: Path) -> int:
  """
  Embed per-document CSP meta into every SPA HTML file under ``frontend_root``.

  Machine-tree pages allow Bokeh ``unsafe-eval``; other paths do not.
  Machine and pub trees allow ``style-src 'unsafe-inline'`` for BokehJS
  runtime ``<style>`` injection (script hashes remain). Policy script hashes
  match that file's inline scripts so SPA heal cannot leave a stale nginx
  header blocking the page.

  Args:
    frontend_root (Path): Public SPA export root (``STATIC_ROOT/frontend``).

  Returns:
    int: Number of HTML files written.

  Examples:
    >>> inject_csp_meta_into_frontend_tree(Path("/no/such/frontend"))
    0
  """
  if not frontend_root.is_dir():
    return 0
  updated = 0
  for path in list_html_files(frontend_root):
    raw = path.read_text(encoding="utf-8")
    without_meta = CSP_META_RE.sub("", raw)
    hashes = extract_inline_csp_hashes_from_html(without_meta)
    try:
      rel = path.relative_to(frontend_root).as_posix()
    except ValueError:
      rel = path.name
    allow_eval = rel == "machine" or rel.startswith("machine/")
    allow_bokeh_style = allow_eval or rel == "pub" or rel.startswith("pub/")
    policy = build_csp_policy(
        **hashes,
        allow_unsafe_eval=allow_eval,
        allow_bokeh_style_inline=allow_bokeh_style,
    )
    next_html = inject_csp_meta_into_html(without_meta, policy)
    if next_html != raw:
      path.write_text(next_html, encoding="utf-8")
      updated += 1
  return updated


def write_spa_csp_includes(
    frontend_root: Path,
    out_dir: Path,
) -> tuple[Path, Path]:
  """
  Write private machine/pub CSP includes under ``out_dir`` (never under ``/static``).

  Args:
    frontend_root (Path): SPA export root used as HTML hash source.
    out_dir (Path): Private nginx include directory (for example ``/etc/nginx``).

  Returns:
    tuple[Path, Path]: Paths to the machine and pub include files written.

  Raises:
    FileNotFoundError: When ``machine/`` or ``pub/`` HTML trees are missing/empty.

  Examples:
    >>> True
    True
  """
  machine_dir = frontend_root / "machine"
  pub_dir = frontend_root / "pub"
  if not list_html_files(machine_dir):
    raise FileNotFoundError(f"no HTML under {machine_dir}")
  if not list_html_files(pub_dir):
    raise FileNotFoundError(f"no HTML under {pub_dir}")

  out_dir.mkdir(parents=True, exist_ok=True)
  machine_hashes = collect_inline_csp_hashes(machine_dir)
  pub_hashes = collect_inline_csp_hashes(pub_dir)
  machine_out = out_dir / "nginx-csp-machine.inc"
  pub_out = out_dir / "nginx-csp-pub.inc"
  machine_out.write_text(
      build_nginx_csp_include(
          **machine_hashes,
          allow_unsafe_eval=True,
          allow_bokeh_style_inline=True,
      ),
      encoding="utf-8",
  )
  pub_out.write_text(
      build_nginx_csp_include(
          **pub_hashes,
          allow_unsafe_eval=False,
          allow_bokeh_style_inline=True,
      ),
      encoding="utf-8",
  )
  return machine_out, pub_out
