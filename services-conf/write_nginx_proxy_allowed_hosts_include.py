#!/usr/bin/env python3
"""Write nginx snippet ``hps-proxy-allowed-hosts.inc`` from hpcperfstats.ini."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from parse_hpcperfstats_proxy_hosts import load_allowed_server_names

GENERATED_HEADER = (
    "# Generated at proxy image build from [DEFAULT] server= in hpcperfstats.ini.\n"
)


def write_allowed_hosts_include(*, ini_path: Path, out_path: Path) -> None:
  names = load_allowed_server_names(ini_path)
  line = f"server_name {' '.join(names)};\n"
  out_path.parent.mkdir(parents=True, exist_ok=True)
  out_path.write_text(GENERATED_HEADER + line, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--ini", type=Path, required=True)
  parser.add_argument("--out", type=Path, required=True)
  args = parser.parse_args(argv)
  try:
    write_allowed_hosts_include(ini_path=args.ini, out_path=args.out)
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
