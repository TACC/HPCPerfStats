#!/usr/bin/env python3
"""
Regenerate SPA hash-based nginx CSP includes from on-volume HTML shells.

Writes **only** under a private nginx include directory (default ``/etc/nginx``).
Never writes into the public ``STATIC_ROOT`` / ``/srv/static`` tree.

Attributes:
  INLINE_SCRIPT_RE: Regex matching inline ``<script>`` bodies (no ``src``).
  INLINE_STYLE_RE: Regex matching ``<style>`` bodies.
  STYLE_ATTR_RE: Regex matching HTML ``style="…"`` attribute values.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
from pathlib import Path

INLINE_SCRIPT_RE = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)</script>",
    re.IGNORECASE,
)
INLINE_STYLE_RE = re.compile(r"<style\b[^>]*>([\s\S]*?)</style>", re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r"\sstyle\s*=\s*(['\"])([\s\S]*?)\1", re.IGNORECASE)


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
    extracted = extract_inline_csp_hashes_from_html(path.read_text(encoding="utf-8"))
    script_hashes.update(extracted["script_hashes"])
    style_hashes.update(extracted["style_hashes"])
    style_attr_hashes.update(extracted["style_attr_hashes"])
  return {
      "script_hashes": sorted(script_hashes),
      "style_hashes": sorted(style_hashes),
      "style_attr_hashes": sorted(style_attr_hashes),
  }


def build_nginx_csp_include(
    *,
    script_hashes: list[str] | None = None,
    style_hashes: list[str] | None = None,
    style_attr_hashes: list[str] | None = None,
    allow_unsafe_eval: bool = False,
) -> str:
  """
  Render an nginx ``add_header Content-Security-Policy …`` include body.

  Args:
    script_hashes (list[str] | None): Quoted ``'sha256-…'`` tokens for scripts.
    style_hashes (list[str] | None): Quoted hashes for ``<style>`` bodies.
    style_attr_hashes (list[str] | None): Quoted hashes for ``style="…"`` attrs.
    allow_unsafe_eval (bool): When True, append ``'unsafe-eval'`` (machine/Bokeh).

  Returns:
    str: Include file text ending with a newline.

  Examples:
    >>> "unsafe-eval" in build_nginx_csp_include(allow_unsafe_eval=True)
    True
  """
  scripts = ["'self'", *(script_hashes or [])]
  if allow_unsafe_eval:
    scripts.append("'unsafe-eval'")
  styles = ["'self'", *(style_hashes or [])]
  if style_attr_hashes:
    styles.extend(["'unsafe-hashes'", *style_attr_hashes])
  policy = "; ".join(
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
  return f'add_header Content-Security-Policy "{policy}" always;\n'


def write_spa_csp_includes(
    frontend_root: Path,
    out_dir: Path,
) -> tuple[Path, Path]:
  """
  Write machine/pub CSP includes under ``out_dir`` (never under public static).

  Args:
    frontend_root (Path): Readable SPA export root (for example
      ``/srv/static/frontend``).
    out_dir (Path): Private nginx include directory (for example ``/etc/nginx``).

  Returns:
    tuple[Path, Path]: Paths to the machine and pub include files written.

  Raises:
    FileNotFoundError: When ``machine/`` or ``pub/`` HTML trees are missing/empty.

  Examples:
    >>> # write_spa_csp_includes(Path("/srv/static/frontend"), Path("/etc/nginx"))
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
      build_nginx_csp_include(**machine_hashes, allow_unsafe_eval=True),
      encoding="utf-8",
  )
  pub_out.write_text(
      build_nginx_csp_include(**pub_hashes, allow_unsafe_eval=False),
      encoding="utf-8",
  )
  return machine_out, pub_out


def main(argv: list[str] | None = None) -> int:
  """
  CLI entry: regenerate SPA CSP includes into a private nginx directory.

  Args:
    argv (list[str] | None): Optional argument vector (defaults to ``sys.argv[1:]``).

  Returns:
    int: Process exit code (0 on success).

  Examples:
    >>> # python3 write_nginx_spa_csp_includes.py --out-dir /etc/nginx
    >>> 0
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--frontend-root",
      type=Path,
      default=Path("/srv/static/frontend"),
      help="SPA static export root (read-only HTML source)",
  )
  parser.add_argument(
      "--out-dir",
      type=Path,
      default=Path("/etc/nginx"),
      help="Private directory for nginx-csp-*.inc (never under /srv/static)",
  )
  args = parser.parse_args(argv)
  try:
    machine_out, pub_out = write_spa_csp_includes(args.frontend_root, args.out_dir)
  except FileNotFoundError as exc:
    print(f"write_nginx_spa_csp_includes: {exc}", file=sys.stderr)
    return 1
  print(f"write_nginx_spa_csp_includes: wrote {machine_out} and {pub_out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
