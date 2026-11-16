"""The ``temp_ini`` fixture leaves conf_parser on the session INI afterwards."""

from __future__ import annotations

import importlib

import pytest

from hpcperfstats.conftest import conf_parser_state_restored
from hpcperfstats.dbload.lib import conf_parser


def test_reload_on_temp_ini_is_undone(
  tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A reload onto a ``timezone = UTC`` INI must not leak past the block."""
  before = conf_parser.get_timezone()
  ini = tmp_path / "hpcperfstats.ini"
  tz = "Pacific/Auckland" if before != "Pacific/Auckland" else "UTC"
  ini.write_text(f"[DEFAULT]\ntimezone = {tz}\n")
  with conf_parser_state_restored(), monkeypatch.context() as mp:
    mp.setenv("HPCPERFSTATS_INI", str(ini))
    importlib.reload(conf_parser)
    assert conf_parser.get_timezone() == tz
  assert conf_parser.get_timezone() == before
