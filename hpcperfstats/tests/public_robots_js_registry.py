"""Read the canonical frontend registry for robots.txt Allow: lines."""
from __future__ import annotations

import re
from pathlib import Path

_JS_REL = Path("hpcperfstats/site/frontend/src/config/publicRobotsAllowPrefixes.ts")


def _repo_root_from(start: Path) -> Path:
  for p in (start, *start.parents):
    if (p / "pyproject.toml").is_file():
      return p
  raise RuntimeError("Could not locate repo root (pyproject.toml)")


def load_public_robots_allow_prefixes() -> tuple[str, ...]:
  root = _repo_root_from(Path(__file__).resolve())
  js_path = root / _JS_REL
  text = js_path.read_text(encoding="utf-8")
  m = re.search(
      r"PUBLIC_ROBOTS_ALLOW_PREFIXES\s*=\s*Object\.freeze\(\s*\[([\s\S]*?)\]\s*\)",
      text,
  )
  if not m:
    raise ValueError(
        "Could not parse PUBLIC_ROBOTS_ALLOW_PREFIXES in {}".format(js_path),
    )
  paths = tuple(re.findall(r"\"(/[^\"]*)\"", m.group(1)))
  if not paths:
    raise ValueError("No path entries in PUBLIC_ROBOTS_ALLOW_PREFIXES")
  return paths


def format_public_robots_txt_body(allow_prefixes: tuple[str, ...]) -> str:
  lines = ["User-agent: *"]
  for prefix in allow_prefixes:
    lines.append("Allow: {}".format(prefix))
  lines.append("Disallow: /")
  return "\n".join(lines)
