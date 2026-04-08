"""Early pytest bootstrap for environment-dependent settings."""

import os


def _default_ini_path():
  repo_root = os.path.dirname(os.path.abspath(__file__))
  local_example = os.path.join(repo_root, "hpcperfstats.ini.example")
  if os.path.exists(local_example):
    return local_example
  return os.path.join(os.path.dirname(repo_root), "hpcperfstats.ini.example")


# Ensure Django settings import can always read required config keys during
# pytest-django's early initialization phase. Do not override an explicit
# operator/env-provided path (e.g. Docker Compose sets
# HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini); overwriting would
# desynchronize conf_parser's cached config from the real archive/queue layout.
if not os.environ.get("HPCPERFSTATS_INI", "").strip():
  os.environ["HPCPERFSTATS_INI"] = _default_ini_path()
