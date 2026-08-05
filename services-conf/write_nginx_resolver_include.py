#!/usr/bin/env python3
"""
Write nginx ``resolver`` include from ``/etc/resolv.conf`` (or a test path).

Attributes:
  GENERATED_HEADER: Comment prefix written at the top of every generated include.
  _NAMESERVER_RE: Compiled regex matching ``nameserver <token>`` resolv.conf lines.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

GENERATED_HEADER = (
    "# Generated at proxy startup from resolv.conf nameserver entries.\n"
)

_NAMESERVER_RE = re.compile(r"^\s*nameserver\s+(\S+)\s*$", re.IGNORECASE)


def _valid_resolver_address(token: str) -> str | None:
  """
  Return a sanitized resolver IP string, or None when the token is unusable.

  Args:
    token (str): Raw nameserver token from resolv.conf.

  Returns:
    str | None: Dotted IPv4/IPv6 literal suitable for nginx ``resolver``, else None.

  Examples:
    >>> _valid_resolver_address("127.0.0.11")
    '127.0.0.11'
    >>> _valid_resolver_address("not-an-ip")
  """
  cleaned = token.strip().strip("%").split("%", 1)[0]
  try:
    parsed = ipaddress.ip_address(cleaned)
  except ValueError:
    return None
  return str(parsed)


def parse_resolv_nameservers(resolv_text: str) -> list[str]:
  """
  Extract unique IP nameserver addresses from resolv.conf text.

  Args:
    resolv_text (str): Full resolv.conf contents.

  Returns:
    list[str]: Deduplicated resolver addresses in file order.

  Examples:
    >>> parse_resolv_nameservers("nameserver 127.0.0.11\\nnameserver 1.1.1.1\\n")
    ['127.0.0.11', '1.1.1.1']
  """
  found: list[str] = []
  seen: set[str] = set()
  for line in resolv_text.splitlines():
    match = _NAMESERVER_RE.match(line)
    if not match:
      continue
    address = _valid_resolver_address(match.group(1))
    if address is None or address in seen:
      continue
    seen.add(address)
    found.append(address)
  return found


def render_resolver_include(nameservers: list[str]) -> str:
  """
  Render an nginx resolver include body for OCSP stapling DNS lookups.

  Args:
    nameservers (list[str]): Validated resolver IP addresses.

  Returns:
    str: nginx include text ending with a newline.

  Raises:
    ValueError: Raised when ``nameservers`` is empty.

  Examples:
    >>> "resolver 127.0.0.11" in render_resolver_include(["127.0.0.11"])
    True
  """
  if not nameservers:
    raise ValueError("no usable nameserver entries found in resolv.conf")
  joined = " ".join(nameservers)
  return (
      f"{GENERATED_HEADER}"
      f"resolver {joined} ipv6=off valid=300s;\n"
      "resolver_timeout 5s;\n"
  )


def write_nginx_resolver_include(
    *,
    resolv_path: Path,
    out_path: Path,
) -> list[str]:
  """
  Read resolv.conf and write a validated nginx resolver include file.

  Args:
    resolv_path (Path): Path to resolv.conf (normally ``/etc/resolv.conf``).
    out_path (Path): Destination include path (normally
        ``/etc/nginx/nginx-resolver.inc``).

  Returns:
    list[str]: Nameserver addresses written into the include.

  Raises:
    ValueError: Raised when resolv.conf is missing or has no usable nameservers.
    OSError: Raised when the resolv file or output path cannot be read/written.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     resolv = Path(tmp) / "resolv.conf"
    ...     out = Path(tmp) / "nginx-resolver.inc"
    ...     _ = resolv.write_text("nameserver 127.0.0.11\\n", encoding="utf-8")
    ...     write_nginx_resolver_include(resolv_path=resolv, out_path=out)
    ['127.0.0.11']
  """
  if not resolv_path.is_file():
    raise ValueError(f"resolv.conf not found: {resolv_path}")
  nameservers = parse_resolv_nameservers(resolv_path.read_text(encoding="utf-8"))
  body = render_resolver_include(nameservers)
  out_path.parent.mkdir(parents=True, exist_ok=True)
  out_path.write_text(body, encoding="utf-8")
  return nameservers


def main(argv: list[str] | None = None) -> int:
  """
  Run the CLI that generates nginx-resolver.inc for the proxy entrypoint.

  Args:
    argv (list[str] | None): Optional argument vector; defaults to ``sys.argv[1:]``.

  Returns:
    int: Process exit code (0 on success, 1 on validation failure).

  Examples:
    >>> main(["--help"])  # doctest: +SKIP
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--resolv",
      type=Path,
      default=Path("/etc/resolv.conf"),
      help="Path to resolv.conf",
  )
  parser.add_argument(
      "--out",
      type=Path,
      default=Path("/etc/nginx/nginx-resolver.inc"),
      help="Destination nginx include path",
  )
  args = parser.parse_args(argv)
  try:
    names = write_nginx_resolver_include(resolv_path=args.resolv, out_path=args.out)
  except (OSError, ValueError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(f"wrote {args.out} with resolvers: {', '.join(names)}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
