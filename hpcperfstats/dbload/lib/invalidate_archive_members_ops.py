"""Host-side helpers for archive-members Redis bulk invalidate + compose restart."""
from __future__ import annotations

import shlex
import subprocess
from typing import Iterable, Optional, Sequence


DEFAULT_COMPOSE_PROJECT = "hpcperfstats"
PIPELINE_SERVICE = "pipeline"
REDIS_SERVICE = "redis"


def compose_argv(
    *,
    project: str = DEFAULT_COMPOSE_PROJECT,
    compose_files: Optional[Sequence[str]] = None,
) -> list[str]:
  """Build ``docker compose -p <project> [-f …]`` argv prefix."""
  argv = ["docker", "compose", "-p", str(project or DEFAULT_COMPOSE_PROJECT)]
  for path in compose_files or ():
    argv.extend(["-f", str(path)])
  return argv


class ComposeRedisCliClient:
  """Minimal Redis client via ``docker compose exec -T redis redis-cli``.

  Implements ``scan_iter`` / ``delete`` for
  :func:`invalidate_archive_members_redis_bulk`.
  """

  def __init__(
      self,
      *,
      compose_dir: str,
      project: str = DEFAULT_COMPOSE_PROJECT,
      compose_files: Optional[Sequence[str]] = None,
      timeout_s: float = 120.0,
  ):
    self.compose_dir = str(compose_dir)
    self.project = str(project or DEFAULT_COMPOSE_PROJECT)
    self.compose_files = list(compose_files or ())
    self.timeout_s = float(timeout_s)

  def _redis_cli(self, *args: str) -> str:
    cmd = compose_argv(project=self.project, compose_files=self.compose_files)
    cmd.extend(["exec", "-T", REDIS_SERVICE, "redis-cli", *args])
    try:
      completed = subprocess.run(
          cmd,
          cwd=self.compose_dir,
          check=False,
          capture_output=True,
          text=True,
          timeout=self.timeout_s,
      )
    except FileNotFoundError as exc:
      raise RuntimeError(
          "docker compose not found on PATH; install Docker or pass --redis-url",
      ) from exc
    except subprocess.TimeoutExpired as exc:
      raise RuntimeError(
          "redis-cli via compose timed out after %.0fs" % self.timeout_s,
      ) from exc
    if completed.returncode != 0:
      err = (completed.stderr or completed.stdout or "").strip()
      raise RuntimeError(
          "compose redis-cli failed (exit %s): %s"
          % (completed.returncode, err or "(no output)"),
      )
    return completed.stdout or ""

  def scan_iter(self, match=None, count=100):
    del count
    pattern = match if match else "*"
    out = self._redis_cli("--scan", "--pattern", str(pattern))
    for line in out.splitlines():
      key = line.strip()
      if key:
        yield key

  def delete(self, *keys: str) -> int:
    if not keys:
      return 0
    deleted = 0
    # redis-cli DEL accepts many keys; keep batches modest for argv limits.
    batch_size = 100
    for i in range(0, len(keys), batch_size):
      chunk = [str(k) for k in keys[i:i + batch_size]]
      out = self._redis_cli("DEL", *chunk)
      try:
        deleted += int((out or "0").strip().splitlines()[-1])
      except (TypeError, ValueError, IndexError):
        deleted += len(chunk)
    return deleted


def restart_pipeline_compose(
    *,
    compose_dir: str,
    project: str = DEFAULT_COMPOSE_PROJECT,
    compose_files: Optional[Sequence[str]] = None,
    timeout_s: float = 300.0,
    run_fn=None,
) -> None:
  """Restart the Compose ``pipeline`` service on the host (clears worker L1)."""
  runner = run_fn or subprocess.run
  cmd = compose_argv(project=project, compose_files=compose_files)
  cmd.extend(["restart", PIPELINE_SERVICE])
  try:
    completed = runner(
        cmd,
        cwd=str(compose_dir),
        check=False,
        capture_output=True,
        text=True,
        timeout=float(timeout_s),
    )
  except FileNotFoundError as exc:
    raise RuntimeError(
        "docker compose not found on PATH; cannot restart pipeline",
    ) from exc
  except subprocess.TimeoutExpired as exc:
    raise RuntimeError(
        "docker compose restart pipeline timed out after %.0fs" % timeout_s,
    ) from exc
  if getattr(completed, "returncode", 1) != 0:
    err = (
        getattr(completed, "stderr", None)
        or getattr(completed, "stdout", None)
        or ""
    ).strip()
    raise RuntimeError(
        "docker compose restart pipeline failed (exit %s): %s"
        % (getattr(completed, "returncode", "?"), err or "(no output)"),
    )


def format_compose_cmd_for_log(argv: Iterable[str]) -> str:
  return " ".join(shlex.quote(str(part)) for part in argv)
