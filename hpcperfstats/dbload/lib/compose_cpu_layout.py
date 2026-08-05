"""
Linear CPU range layout for Docker Compose ``cpuset`` (db/web-first, pipeline
last).

Used by ``scripts/apply_compose_cpu_pinning.py``. Independent of NUMA sysfs;
``proxy`` uses the same cpuset string as ``web`` (allowed overlap between
containers).
"""
from __future__ import annotations

from typing import Dict


def _cpuset_range(lo: int, hi: int) -> str:
  """
  Inclusive integer range as Linux cpuset string.
  
  Args:
    lo (int): Integer value for lo.
    hi (int): Integer value for hi.
  
  Returns:
    str: str produced by this call.
  
  Raises:
    ValueError: Raised when ``_cpuset_range`` hits a ``ValueError`` failure
    path.
  
  Examples:
    >>> _cpuset_range(0, 0)  # doctest: +SKIP
  """
  if lo > hi:
    raise ValueError("invalid range")
  if lo == hi:
    return str(lo)
  return "%d-%d" % (lo, hi)


def partition_responsive_cpusets(total_cpus: int) -> Dict[str, str]:
  """
  Partition ``0 .. total_cpus-1`` (proxy duplicates web).
  
  Allocates **db** and **web** first, then optional **redis** / **rabbitmq**
    slices,
  then **pipeline** for the remainder. If redis or rabbitmq count is zero, that
  service reuses the **pipeline** cpuset (overlap is allowed between
    containers).
  
  Args:
    total_cpus (int): Integer value for total cpus.
  
  Returns:
    Dict[str, str]: Dict[str, str] produced by this call.
  
  Raises:
    ValueError: Raised when ``partition_responsive_cpusets`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> partition_responsive_cpusets(0)  # doctest: +SKIP
  """
  n = total_cpus
  if n < 1:
    raise ValueError("total_cpus must be at least 1")
  if n <= 3:
    full = "0" if n == 1 else _cpuset_range(0, n - 1)
    return {
        "db": full,
        "web": full,
        "proxy": full,
        "redis": full,
        "rabbitmq": full,
        "pipeline": full,
    }

  redis_n = 2 if n >= 24 else (1 if n >= 14 else 0)
  rabbit_n = 2 if n >= 24 else (1 if n >= 14 else 0)
  db_n = max(1, int(n * 0.28))
  web_n = max(1, int(n * 0.28))
  pipe_min = 1

  fixed_aux = redis_n + rabbit_n
  while db_n + web_n + fixed_aux + pipe_min > n:
    if redis_n > 0:
      redis_n -= 1
    elif rabbit_n > 0:
      rabbit_n -= 1
    elif web_n > 1:
      web_n -= 1
    elif db_n > 1:
      db_n -= 1
    else:
      break
    fixed_aux = redis_n + rabbit_n

  pipe_n = n - db_n - web_n - redis_n - rabbit_n
  while pipe_n < 1 and web_n > 1:
    web_n -= 1
    pipe_n = n - db_n - web_n - redis_n - rabbit_n
  while pipe_n < 1 and db_n > 1:
    db_n -= 1
    pipe_n = n - db_n - web_n - redis_n - rabbit_n
  if pipe_n < 1:
    full = _cpuset_range(0, n - 1)
    return {
        "db": full,
        "web": full,
        "proxy": full,
        "redis": full,
        "rabbitmq": full,
        "pipeline": full,
    }

  cur = 0

  def take(count: int) -> str:
    """
    Take the next item from this partition.
    
    Args:
      count (int): Integer value for count.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> take(0)  # doctest: +SKIP
    """
    nonlocal cur
    s = _cpuset_range(cur, cur + count - 1)
    cur += count
    return s

  db_s = take(db_n)
  web_s = take(web_n)
  if redis_n > 0:
    redis_s = take(redis_n)
  if rabbit_n > 0:
    rabbit_s = take(rabbit_n)
  pipeline_s = _cpuset_range(cur, n - 1)
  cur = n

  if redis_n <= 0:
    redis_s = pipeline_s
  if rabbit_n <= 0:
    rabbit_s = pipeline_s

  return {
      "db": db_s,
      "web": web_s,
      "proxy": web_s,
      "redis": redis_s,
      "rabbitmq": rabbit_s,
      "pipeline": pipeline_s,
  }
