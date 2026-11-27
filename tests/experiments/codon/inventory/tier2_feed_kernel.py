"""Tier-2: compile bounded feed kernel (same module as experiments root)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parse_kernel import feed_fingerprint  # noqa: E402
from e6_corpus import build_e6_payload_lines  # noqa: E402


def main():
  fp = feed_fingerprint(build_e6_payload_lines(20))
  print(fp["stats_rows"], fp["proc_rows"])


if __name__ == "__main__":
  main()
