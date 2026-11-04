#!/usr/bin/env python3
"""
Rewrite a site hpcperfstats.ini onto the current hpcperfstats.ini.example.

The output is the whole example file (comments, order, new keys) with only
the site's own settings filled in:

* a site value equal to the code default (``INI_OPTION_DEFAULTS``) is left as
  the example's commented ``# key = default`` line;
* a site value that differs from a default goes on an active ``key = value``
  line directly below that commented default, so the default stays visible;
* a required key (no code default) replaces the example's placeholder line;
* a key the site file sets in an old section (``host`` under ``[PORTAL]``)
  moves to the example's section;
* site keys the example no longer documents are dropped and listed on stdout.

The effective value of every registry key (ini value, else default) is
checked to be identical before and after; on a mismatch nothing is written.
In place, the old file is kept as ``<ini>.bak.<UTC timestamp>``.

Needs the project Python (3.14). On a deploy host, which has none, run it
through ``scripts/rewrite_hpcperfstats_ini.sh``: that runs this file from the
checkout inside a throwaway container of the release image.

Examples (from the checkout root)::

  ./scripts/rewrite_hpcperfstats_ini.sh --dry-run
  .venv/bin/python3 scripts/rewrite_hpcperfstats_ini.py hpcperfstats.ini

Attributes:
  _REPO_ROOT: Checkout root, put on ``sys.path`` so ``hpcperfstats`` imports.
  NO_DEFAULT_SECTION: ``default_section`` name that makes ``[DEFAULT]`` an
    ordinary section, so its keys do not leak into every other section.
  ACTIVE_OPTION_RE: Matches an uncommented ``key = value`` line.
  COMMENTED_OPTION_RE: Matches a commented ``# key = value`` line.
  SECTION_RE: Matches a ``[SECTION]`` header line.
"""

from __future__ import annotations

import argparse
import configparser
import datetime
import difflib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(_REPO_ROOT))

from hpcperfstats.dbload.lib import conf_parser  # noqa: E402

NO_DEFAULT_SECTION = "__hpcperfstats_no_default_section__"
ACTIVE_OPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")
COMMENTED_OPTION_RE = re.compile(r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


@dataclass
class RewriteResult:
  """
  Output of ``rewrite_from_example``.

  Attributes:
    text (str): The rewritten ini text.
    dropped (list[tuple[str, str]]): ``(section, option)`` site keys the
      example does not document; not written.
    missing_required (list[tuple[str, str]]): Required keys (no code
      default) the site file lacks; the example's placeholder line is kept.
  """

  text: str
  dropped: list[tuple[str, str]] = field(default_factory=list)
  missing_required: list[tuple[str, str]] = field(default_factory=list)


def parse_ini_text(text: str) -> configparser.ConfigParser:
  """
  Parse ini text raw, with ``[DEFAULT]`` as an ordinary section.

  Values keep ``%(name)s`` references unexpanded, and ``[DEFAULT]`` keys are
  not inherited by the other sections, so each key is seen only where the
  file writes it.

  Args:
    text (str): Ini file contents.

  Returns:
    configparser.ConfigParser: The parsed file.

  Examples:
    >>> parse_ini_text("[DEFAULT]\\nmachine = m\\n").get("DEFAULT", "machine")
    'm'
  """
  parser = configparser.ConfigParser(
    interpolation=None, default_section=NO_DEFAULT_SECTION
  )
  parser.read_string(text)
  return parser


def value_equals_default(value: str, default: str | None) -> bool:
  """
  Return True when an ini *value* equals the registry *default*.

  Same comparison as the example drift test: exact string, else float-equal
  (``2.0`` equals ``2``). A ``None`` default (required key) never matches.

  Args:
    value (str): Site value as written in the ini.
    default (str | None): Registry default, or ``None`` for a required key.

  Returns:
    bool: Whether the value is the default.

  Examples:
    >>> value_equals_default("2.0", "2")
    True
    >>> value_equals_default("db", "/run/postgresql")
    False
  """
  if default is None:
    return False
  if value.strip() == default:
    return True
  try:
    return float(value) == float(default)
  except TypeError, ValueError:
    return False


def _registry() -> dict[str, tuple[str, str | None]]:
  """
  Map each registry option to its ``(section, default)``.

  Returns:
    dict[str, tuple[str, str | None]]: Option name to section and default.

  Examples:
    >>> _registry()["host"]
    ('DEFAULT', '/run/postgresql')
  """
  return {
    option: (section, default)
    for section, option, default in conf_parser.INI_OPTION_REGISTRY
  }


def _site_values(
  site: configparser.ConfigParser,
  registry: dict[str, tuple[str, str | None]],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
  """
  Collect the site's value for each registry option, from any section.

  The registry section wins when the key appears in more than one section;
  otherwise the key is taken from whichever (legacy) section has it.

  Args:
    site (configparser.ConfigParser): Site ini from ``parse_ini_text``.
    registry (dict[str, tuple[str, str | None]]): From ``_registry``.

  Returns:
    tuple[dict[str, str], list[tuple[str, str]]]: Option to raw value, and
    the ``(section, option)`` pairs that are not registry options.

  Examples:
    >>> _site_values(parse_ini_text("[PORTAL]\\nhost = db\\n"), _registry())
    ({'host': 'db'}, [])
  """
  values: dict[str, str] = {}
  dropped: list[tuple[str, str]] = []
  for section in site.sections():
    for option, value in site.items(section):
      if option not in registry:
        dropped.append((section, option))
      elif option not in values or registry[option][0] == section:
        values[option] = value
  return values, dropped


def _format_option(option: str, value: str) -> str:
  """
  Render ``key = value``, indenting continuation lines of a multi-line value.

  Args:
    option (str): Ini option name.
    value (str): Raw value; embedded newlines become continuation lines.

  Returns:
    str: One or more ini lines without a trailing newline.

  Examples:
    >>> _format_option("allow_from", "10.0.0.0/8\\n192.168.0.0/16")
    'allow_from = 10.0.0.0/8\\n  192.168.0.0/16'
  """
  first, *rest = value.split("\n")
  return "\n".join([f"{option} = {first}".rstrip()] + [f"  {r}" for r in rest])


def rewrite_from_example(
  site: configparser.ConfigParser, example_text: str
) -> RewriteResult:
  """
  Return the example text with the site's non-default settings filled in.

  Walks the example line by line. For the first line documenting a registry
  option (active or commented): absent or default site value -> the example's
  commented default line (an active example line is commented as
  ``# key = default``); non-default site value -> the commented default line
  followed by ``key = site value``; required key -> ``key = site value`` in
  place of the placeholder. Continuation lines of an active example value
  are dropped with it. Every other line is copied verbatim.

  Args:
    site (configparser.ConfigParser): Site ini from ``parse_ini_text``.
    example_text (str): Contents of ``hpcperfstats.ini.example``.

  Returns:
    RewriteResult: Rewritten text plus dropped and missing-required keys.

  Examples:
    >>> site = parse_ini_text("[DEFAULT]\\nmachine = m\\n")
    >>> ex = "[DEFAULT]\\n# Port.\\n# port = 5432\\n"
    >>> rewrite_from_example(site, ex).text
    '[DEFAULT]\\n# Port.\\n# port = 5432\\n'
  """
  registry = _registry()
  values, dropped = _site_values(site, registry)
  result = RewriteResult(text="", dropped=dropped)
  out: list[str] = []
  done: set[str] = set()
  skipping_continuation = False
  for line in example_text.splitlines():
    if skipping_continuation and line[:1] in (" ", "\t") and line.strip():
      continue
    skipping_continuation = False
    active = ACTIVE_OPTION_RE.match(line)
    match = active or COMMENTED_OPTION_RE.match(line)
    option = match.group(1) if match else None
    if option not in registry:
      out.append(line)
      continue
    skipping_continuation = bool(active)
    default = registry[option][1]
    commented = line if not active else f"# {option} = {default}"
    if option in done:
      if not active:
        out.append(line)
      continue
    done.add(option)
    value = values.get(option)
    if default is None:
      if value is None:
        out.append(line)
        if active:
          result.missing_required.append((registry[option][0], option))
      else:
        out.append(_format_option(option, value))
    elif value is None or value_equals_default(value, default):
      out.append(commented)
    else:
      out.extend([commented, _format_option(option, value)])
  result.text = "\n".join(out) + "\n"
  return result


def effective_values(site: configparser.ConfigParser) -> dict[str, str | None]:
  """
  Return each registry option's effective raw value: site value, else default.

  Float-equal values are normalized to the default string so ``2.0`` and a
  commented ``# key = 2`` compare equal.

  Args:
    site (configparser.ConfigParser): Parsed ini from ``parse_ini_text``.

  Returns:
    dict[str, str | None]: Option name to effective value.

  Examples:
    >>> effective_values(parse_ini_text("[PORTAL]\\nhost = db\\n"))["host"]
    'db'
  """
  registry = _registry()
  values, _dropped = _site_values(site, registry)
  effective: dict[str, str | None] = {}
  for option, (_section, default) in registry.items():
    value = values.get(option)
    if value is None or value_equals_default(value, default):
      effective[option] = default
    else:
      effective[option] = value
  return effective


def changed_effective_values(
  site: configparser.ConfigParser, result: RewriteResult
) -> list[str]:
  """
  Return registry options whose effective value the rewrite would change.

  Required keys the site lacks are excluded: the example placeholder kept
  for them is reported through ``result.missing_required`` instead.

  Args:
    site (configparser.ConfigParser): Site ini from ``parse_ini_text``.
    result (RewriteResult): Output of ``rewrite_from_example`` for *site*.

  Returns:
    list[str]: Sorted option names that differ; empty when the rewrite is
    behavior-preserving.

  Examples:
    >>> site = parse_ini_text("[PORTAL]\\nhost = db\\n")
    >>> res = rewrite_from_example(
    ...   site, "[DEFAULT]\\n# host = /run/postgresql\\n"
    ... )
    >>> changed_effective_values(site, res)
    []
  """
  missing = {option for _section, option in result.missing_required}
  before = effective_values(site)
  after = effective_values(parse_ini_text(result.text))
  return sorted(k for k in before if before[k] != after[k] and k not in missing)


def main(argv: list[str] | None = None) -> int:
  """
  CLI entry: rewrite (or with ``--dry-run`` diff) a site ini onto the example.

  Args:
    argv (list[str] | None): Arguments without the program name; ``None``
      reads ``sys.argv[1:]``.

  Returns:
    int: ``0`` on success, ``1`` when the effective-value check fails.

  Examples:
    >>> main(["hpcperfstats.ini", "--dry-run"])  # doctest: +SKIP
    0
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  parser.add_argument("ini", type=Path, help="site hpcperfstats.ini to rewrite")
  parser.add_argument(
    "--example",
    type=Path,
    default=_REPO_ROOT / "hpcperfstats.ini.example",
    help="example file to rewrite onto (default: checkout example)",
  )
  parser.add_argument(
    "--output", type=Path, help="write here instead of rewriting INI in place"
  )
  parser.add_argument(
    "--dry-run", action="store_true", help="print a diff, write nothing"
  )
  args = parser.parse_args(argv)

  original = args.ini.read_text(encoding="utf-8")
  site = parse_ini_text(original)
  result = rewrite_from_example(site, args.example.read_text(encoding="utf-8"))

  changed = changed_effective_values(site, result)
  if changed:
    print(f"ERROR: effective values would change: {changed}", file=sys.stderr)
    return 1
  for section, option in result.dropped:
    print(f"dropped (not in example): [{section}] {option}")
  for section, option in result.missing_required:
    print(f"WARNING: required [{section}] {option} missing; example line kept")

  target = args.output or args.ini
  if args.dry_run:
    sys.stdout.writelines(
      difflib.unified_diff(
        original.splitlines(keepends=True),
        result.text.splitlines(keepends=True),
        fromfile=str(args.ini),
        tofile=str(target),
      )
    )
    return 0
  if target == args.ini:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = args.ini.with_name(f"{args.ini.name}.bak.{stamp}")
    backup.write_text(original, encoding="utf-8")
    print(f"backup: {backup}")
  tmp = target.with_name(f".{target.name}.rewrite.tmp")
  tmp.write_text(result.text, encoding="utf-8")
  os.replace(tmp, target)
  print(f"wrote: {target}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
