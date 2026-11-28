"""PG18 default db and the legacy PG15 include: compose wiring and runbook.

Merges follow podman-compose 1.5.0 (``rec_merge_one``): the site file loads
first and its ``include:`` entries after it; later files win on scalars,
lists append, ``volumes`` drop earlier entries with the same mount target,
``command``/``entrypoint`` and ``!override`` replace.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULTS = REPO / "docker-compose.defaults.yaml"
LEGACY = REPO / "docker-compose.pg15-primary.yaml"
EXAMPLE = REPO / "docker-compose.yaml.example"
RUNBOOK = REPO / "docs" / "how-to" / "operate" / "migrate-to-postgresql-18.md"
INCLUDE_LINE = "  - docker-compose.defaults.yaml"
LEGACY_LINE = "  - docker-compose.pg15-primary.yaml"
HUB_PG15 = "docker.io/timescale/timescaledb:2.28.3-pg15"
PG18_TAG = "hpcperfstats-db:guard1"
PG15_VOLUME_DECL = (
  "  postgres_data:\n    driver: local\n    driver_opts:\n      type: none\n"
  "      device: /data/hpcperfstats_db/pg15\n      o: bind\n"
)


class _Override:
  def __init__(self, value: Any) -> None:
    self.value = value


class _Reset:
  pass


class _Loader(yaml.SafeLoader):
  """SafeLoader that keeps the compose ``!override`` / ``!reset`` tags."""


_Loader.add_constructor(
  "!override",
  lambda loader, node: _Override(
    loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node, deep=True)
  ),
)
_Loader.add_constructor("!reset", lambda loader, node: _Reset())


def _load(text: str) -> dict[str, Any]:
  return yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass


def _strip_tags(value: Any) -> Any:
  if isinstance(value, _Override):
    return _strip_tags(value.value)
  if isinstance(value, dict):
    return {
      k: _strip_tags(v) for k, v in value.items() if not isinstance(v, _Reset)
    }
  if isinstance(value, list):
    return [_strip_tags(v) for v in value]
  return value


def _norm_env(service: dict[str, Any]) -> None:
  env = service.get("environment")
  if isinstance(env, list):
    service["environment"] = dict(e.split("=", 1) for e in env)


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
  """Merge ``source`` into ``target`` like podman-compose ``rec_merge_one``."""
  for key, value2 in source.items():
    if key not in target:
      target[key] = copy.deepcopy(value2)
      continue
    value = target[key]
    if isinstance(value2, _Reset):
      del target[key]
    elif isinstance(value2, _Override):
      target[key] = copy.deepcopy(value2.value)
    elif key in ("command", "entrypoint"):
      target[key] = copy.deepcopy(value2)
    elif isinstance(value2, list):
      if key == "volumes":
        pts = {v.split(":", 2)[1] for v in value2 if ":" in v}
        value[:] = [v for v in value if v.split(":", 2)[1] not in pts]
      value.extend(copy.deepcopy(value2))
    elif isinstance(value2, dict):
      _merge(value, value2)
    else:
      target[key] = value2


def _merged(site_text: str, profiles: tuple[str, ...] = ()) -> dict[str, Any]:
  """Return the merged services of a site file and its includes.

  Args:
    site_text (str): Site ``docker-compose.yaml`` text.
    profiles (tuple[str, ...]): Active ``--profile`` names.

  Returns:
    dict[str, Any]: Active services after the merge, tags resolved.
  """
  docs = [_load(site_text)]
  docs += [_load((REPO / f).read_text()) for f in docs[0].get("include", [])]
  merged: dict[str, Any] = {}
  for doc in docs:
    for svc in (doc.get("services") or {}).values():
      if isinstance(svc, dict):
        _norm_env(svc)
    _merge(merged, {k: v for k, v in doc.items() if k != "include"})
  services = _strip_tags(merged["services"])
  return {
    name: svc
    for name, svc in services.items()
    if not svc.get("profiles") or set(svc["profiles"]) & set(profiles)
  }


def _legacy_site() -> str:
  text = EXAMPLE.read_text()
  text = text.replace(INCLUDE_LINE + "\n", f"{INCLUDE_LINE}\n{LEGACY_LINE}\n")
  return text.replace("volumes:\n", "volumes:\n" + PG15_VOLUME_DECL, 1)


def _mounts(svc: dict[str, Any]) -> dict[str, str]:
  """Map mount target -> source for a service's volumes."""
  out = {}
  for vol in svc.get("volumes", []):
    src, dst = vol.split(":", 2)[:2]
    out[dst.rstrip("/") or "/"] = src
  return out


def _gucs(command: list[str]) -> dict[str, str]:
  return {
    command[i + 1].split("=", 1)[0]: command[i + 1].split("=", 1)[1]
    for i in range(len(command) - 1)
    if command[i] == "-c"
  }


def _assert_db_mounts(services: dict[str, Any]) -> None:
  """Every PG18 service mounts only the PG18 bind; Hub PG15 only PG15."""
  seen = 0
  for name, svc in services.items():
    image = str(svc.get("image", ""))
    mounts = _mounts(svc)
    if image.startswith("hpcperfstats-db"):
      seen += 1
      assert mounts.get("/var/lib/postgresql") == "postgres_data_pg18", name
      assert "postgres_data" not in mounts.values(), name
      assert not any(m.startswith("/var/lib/postgresql/") for m in mounts), name
    elif image == HUB_PG15:
      seen += 1
      assert mounts["/var/lib/postgresql/data"] == "postgres_data", name
      assert "postgres_data_pg18" not in mounts.values(), name
      assert mounts["/run/postgresql"] == "postgres_runtime"
      assert "/etc/postgresql/pg_hba.conf" in mounts
      assert mounts["/usr/local/bin/hpc-pg15-guard.sh"].endswith(
        "db-docker-pg15-guard.sh"
      )
  assert seen, "no database service found"


@pytest.mark.parametrize("profiles", [(), ("pg18-migrate",)])
@pytest.mark.parametrize("site", ["new-install", "legacy"])
def test_pg18_never_mounts_pg15_volume(
  site: str, profiles: tuple[str, ...]
) -> None:
  """Regression (owner): no PG18 server may ever be wired to PG15 data."""
  text = EXAMPLE.read_text() if site == "new-install" else _legacy_site()
  _assert_db_mounts(_merged(text, profiles))


def test_new_install_db_is_guarded_pg18_primary() -> None:
  services = _merged(EXAMPLE.read_text(), ("pg18-migrate",))
  assert "db_pg18" not in services
  db = services["db"]
  assert db["image"] == PG18_TAG and db["pull_policy"] == "never"
  assert db["build"]["dockerfile"] == "db.Dockerfile"
  assert db["environment"]["HPCPERFSTATS_DB_ROLE"] == "primary"
  assert "profiles" not in db
  assert db["networks"]["hpcperfstats_net"]["aliases"] == ["db"]
  assert _mounts(db)["/run/postgresql"] == "postgres_runtime"
  assert "entrypoint" not in db
  gucs = _gucs(db["command"])
  assert gucs["io_method"] == "worker" and int(gucs["io_workers"]) >= 1
  assert gucs["hba_file"] == "/etc/postgresql/pg_hba.conf"
  assert db["shm_size"] == "16gb" and db["stop_grace_period"] == "30m"


def test_legacy_overlay_keeps_pg15_as_db() -> None:
  services = _merged(_legacy_site(), ("pg18-migrate",))
  db = services["db"]
  assert db["image"] == HUB_PG15
  assert db["pull_policy"] == "missing"
  assert db["build"]["dockerfile"] == "db-pg15-legacy.Dockerfile"
  assert db["entrypoint"] == ["/bin/sh", "/usr/local/bin/hpc-pg15-guard.sh"]
  assert db["environment"]["HPCPERFSTATS_DB_ROLE"] == "pg15-legacy"
  assert db["networks"]["hpcperfstats_net"]["aliases"] == ["db"]
  assert "io_method" not in _gucs(db["command"])
  pg18 = services["db_pg18"]
  assert pg18["profiles"] == ["pg18-migrate"]
  assert pg18["image"] == PG18_TAG and pg18["pull_policy"] == "never"
  assert pg18["build"]["dockerfile"] == "db.Dockerfile"
  assert pg18["environment"]["HPCPERFSTATS_DB_ROLE"] == "migrate-target"
  assert pg18["networks"]["hpcperfstats_net"]["aliases"] == ["db18"]
  # Two servers in one socket dir collide on .s.PGSQL.5432.lock.
  assert "/run/postgresql" not in _mounts(pg18)
  assert "db_pg18" not in _merged(_legacy_site())


def test_legacy_dockerfile_only_retags_hub_image() -> None:
  lines = [
    ln
    for ln in (REPO / "services-conf" / "db-pg15-legacy.Dockerfile")
    .read_text()
    .splitlines()
    if ln.strip() and not ln.startswith("#")
  ]
  assert lines == [f"FROM {HUB_PG15}"]


def test_gucs_match_across_pg18_db_overlay_db_pg18_and_pg15_db() -> None:
  """db_pg18 becomes db at cutover; PG15 shares every non-io GUC (12GB bug)."""
  defaults_db = _gucs(_load(DEFAULTS.read_text())["services"]["db"]["command"])
  legacy = _load(LEGACY.read_text())["services"]
  assert _gucs(legacy["db_pg18"]["command"]) == defaults_db
  pg15 = _gucs(legacy["db"]["command"])
  assert pg15 == {
    k: v for k, v in defaults_db.items() if k not in ("io_method", "io_workers")
  }


def test_defaults_declare_no_postgres_data_volumes() -> None:
  """Volume objects come from the site file; a missing bind fails closed."""
  volumes = _load(DEFAULTS.read_text())["volumes"]
  assert not {"postgres_data", "postgres_data_pg18"} & set(volumes)
  assert "postgres_runtime" in volumes
  assert "db_pg18" not in _load(DEFAULTS.read_text())["services"]


def test_example_declares_only_the_pg18_bind() -> None:
  doc = _load(EXAMPLE.read_text())
  assert "postgres_data" not in doc["volumes"]
  pg18 = doc["volumes"]["postgres_data_pg18"]["driver_opts"]
  assert pg18["o"] == "bind" and pg18["device"] == "/data/hpcperfstats_db/pg18"
  assert not {"db", "db_pg18"} & set(doc["services"])
  assert doc["include"] == ["docker-compose.defaults.yaml"]


def test_guard_files_tracked_and_old_overlay_gone() -> None:
  assert not (REPO / "docker-compose.pg18-primary.yaml").exists()
  for rel in (
    "services-conf/db-docker-pg15-guard.sh",
    "services-conf/db-pg15-legacy.Dockerfile",
    "docker-compose.pg15-primary.yaml",
  ):
    assert (REPO / rel).is_file(), rel
    ignored = subprocess.run(
      ["git", "check-ignore", "-q", rel], cwd=REPO, check=False
    )
    assert ignored.returncode == 1, f"{rel} is gitignored"


def _podman_compose_config(
  tmp_path: Path, site: str, file_flag: list[str]
) -> dict[str, Any]:
  for name in (DEFAULTS.name, LEGACY.name):
    shutil.copy(REPO / name, tmp_path / name)
  (tmp_path / "docker-compose.yaml").write_text(site)
  res = subprocess.run(
    ["podman-compose", *file_flag, "--profile", "pg18-migrate", "config"],
    cwd=tmp_path,
    capture_output=True,
    text=True,
    check=True,
  )
  return yaml.safe_load(res.stdout)["services"]


@pytest.mark.skipif(
  shutil.which("podman-compose") is None, reason="podman-compose not installed"
)
@pytest.mark.parametrize("file_flag", [[], ["-f", "docker-compose.yaml"]])
@pytest.mark.parametrize("site", ["new-install", "legacy"])
def test_real_podman_compose_config_matches(
  tmp_path: Path, site: str, file_flag: list[str]
) -> None:
  """Same contract through the installed podman-compose (incl. ``-f``)."""
  text = EXAMPLE.read_text() if site == "new-install" else _legacy_site()
  services = _podman_compose_config(tmp_path, text, file_flag)
  for svc in services.values():
    svc["volumes"] = [
      re.sub(r"^/\S*/services-conf/", "./services-conf/", v)
      for v in svc.get("volumes", [])
    ]
  _assert_db_mounts(services)
  db = services["db"]
  want = HUB_PG15 if site == "legacy" else PG18_TAG
  assert db["image"] == want
  assert ("db_pg18" in services) == (site == "legacy")


def _runbook_logical_lines() -> str:
  """Runbook text with shell backslash continuations joined into one line."""
  return re.sub(r"\\\n\s*", " ", RUNBOOK.read_text())


def test_runbook_uses_podman_compose_without_file_flag() -> None:
  """`-f docker-compose.yaml` / docker compose forms drift from prod hosts."""
  text = RUNBOOK.read_text()
  assert "docker compose" not in text
  assert not re.search(r"podman-compose[^\n]*-f docker-compose", text)
  assert "podman-compose -p hpcperfstats" in text


def test_runbook_references_existing_artifacts() -> None:
  text = RUNBOOK.read_text()
  assert "docs/snippets/" not in text  # snippet never existed
  blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
  assert blocks and not any("override" in b for b in blocks)
  for rel in (
    "docker-compose.pg15-primary.yaml",
    "scripts/pg18_host_data_chunk_copy.py",
    "scripts/pg18_write_primary_marker.sh",
    "scripts/rebuild_full_site.sh",
  ):
    assert rel in text and (REPO / rel).is_file(), rel
  assert "pg18-primary.yaml" not in text
  assert "pg15-retired" not in text
  # The release step inserts after this exact example include line.
  assert INCLUDE_LINE in EXAMPLE.read_text().splitlines()
  assert LEGACY_LINE.strip() in text


def test_runbook_release_step_adds_include_before_pull() -> None:
  release = (
    RUNBOOK.read_text().split("## Release step", 1)[1].split("\n## ", 1)[0]
  )
  order = [
    "podman stop -t 30 $C",
    "stop db_pg18",
    'sed -i "/^  - docker-compose.defaults.yaml',
    "git pull --ff-only",
    "./scripts/rebuild_full_site.sh",
    "up -d --no-build db_pg18",
  ]
  pos = [release.index(s) for s in order]
  assert pos == sorted(pos), list(zip(order, pos, strict=True))


def test_runbook_freeze_and_cutover_mechanics() -> None:
  text = RUNBOOK.read_text()
  d2 = text.split("### D2", 1)[1].split("### D3", 1)[0]
  assert "ALTER SYSTEM SET default_transaction_read_only = on" in d2
  assert "pg_reload_conf()" in d2 and "pg15_readonly_ok" in d2
  assert "tup_inserted" in d2
  e1 = text.split("### E1", 1)[1].split("### E2", 1)[0]
  steps = [
    "stop web proxy pipeline",
    "--check-writes",
    "stop db_pg18",
    "&& $M --dry-run && $M",
    "podman rm hpcperfstats_db_pg18_1",
    "up -d --no-build db",
    "up -d --no-build web",
    "indisunique AND indisvalid",
    "add_compression_policy",
    "up -d --no-build pipeline proxy",
  ]
  pos = [e1.index(s) for s in steps]
  assert pos == sorted(pos), list(zip(steps, pos, strict=True))
  e2 = text.split("### E2", 1)[1].split("\n## ", 1)[0]
  assert "pg18_write_primary_marker.sh --rollback" in e2
  assert "ALTER SYSTEM RESET default_transaction_read_only" in e2


def test_runbook_db_stops_keep_grace_period() -> None:
  """A plain `podman stop` SIGKILLs PostgreSQL after 10 s."""
  for line in _runbook_logical_lines().splitlines():
    for m in re.finditer(r"podman (stop|rm -f)\b([^;&|\n]*)", line):
      args = m.group(2)
      if "_db_" in args or "_db_1" in args:
        assert m.group(1) == "stop" and "-t 1800" in args, line


def test_runbook_up_commands_name_their_services() -> None:
  """Bare `up -d` after a compose edit recreates rabbitmq/valkey too."""
  for line in _runbook_logical_lines().splitlines():
    for cmd in re.findall(r"podman-compose[^;&|\n]*? up -d[^;&|\n]*", line):
      words = [w for w in cmd.split(" up -d", 1)[1].split() if w]
      services = [w for w in words if not w.startswith("-")]
      assert services, f"bare up: {cmd}"


def test_runbook_python_blocks_avoid_parameterized_django_sql() -> None:
  """Regression (02, 2026-11-06): web cursor rejected execute(sql, params).

  Phase B checks failed with "the query has 1 placeholders but 2 parameters
  were passed"; verification SQL belongs in psql inside db_pg18.
  """
  text = RUNBOOK.read_text()
  for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
    if "/usr/local/bin/python3" in block and "execute(" in block:
      assert not re.search(r'execute\("[^"]*",\s*[\[(]', block), block[:200]
  phase_b = text.split("## Phase B", 1)[1].split("## Phase C", 1)[0]
  assert "remove_compression_policy('host_data', if_exists => true)" in phase_b
  assert "compression_jobs=" in phase_b and "host_data_n=" in phase_b


def test_cutover_gated_on_restored_unique_index() -> None:
  """Side-agent catch (2026-11-11): Phase B drops the PG18 indexes; E1 must
  not start ingest unless a valid UNIQUE index is back (ON CONFLICT)."""
  text = RUNBOOK.read_text()
  e1 = text.split("### E1", 1)[1].split("### E2", 1)[0]
  gate = e1.index("indisunique AND indisvalid")
  assert gate < e1.index("up -d --no-build pipeline proxy")
  assert 'if [ "${uniq:-0}" -ge 1 ] && [ "$bad" = "0" ]; then' in e1
  assert "CUTOVER_BLOCKED" in e1
  assert text.index("### C2") < text.index("### D1")
  d3 = text.split("### D3", 1)[1].split("### D4", 1)[0]
  assert "refusing" in d3 and "--verify" in d3


def test_runbook_detached_runs_disable_inherited_restart() -> None:
  """Side-agent catch (2026-11-15): one-shot `run -d` containers inherit
  web's restart: always, so a finished copy restarted another full pass and
  a failing C2 looped on hpcperfstats02. Every detached start must turn it off."""
  text = _runbook_logical_lines()
  starts = re.findall(r"run -d [^\n]*?--name (\S+)[^\n]*", text)
  assert starts, "no detached runs found"
  for line in re.findall(r"[^\n]*run -d [^\n]*--name \S+[^\n]*", text):
    name = re.search(r"--name (\S+)", line).group(1)
    assert f"podman update --restart=no {name}" in line, name
