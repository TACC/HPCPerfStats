"""
XALT database models: run, join_run_object, lib. Used for executable path and
  library info per job (read via views).
"""
from __future__ import annotations

from typing import Any

from django.db import models


class run(models.Model):
  """
  XALT run record: job_id, exec_path, cwd, times, user, etc.
  """
  # Mirrors upstream XALT MySQL schema (see upstream `py_src/createDB.in.py`).
  class Meta:
    """
    Django model metadata for the enclosing model.
    """
    managed = False
    db_table = "xalt_run"

  run_id = models.PositiveIntegerField(primary_key=True)
  job_id = models.CharField(max_length=64)
  run_uuid = models.CharField(max_length=36)

  date = models.DateTimeField()
  syshost = models.CharField(max_length=64)
  uuid = models.CharField(max_length=36, null=True)
  hash_id = models.CharField(max_length=40)

  account = models.CharField(max_length=20)
  exec_type = models.CharField(max_length=7)
  start_time = models.FloatField()
  end_time = models.FloatField()
  run_time = models.FloatField()
  probability = models.FloatField()

  num_cores = models.PositiveIntegerField()
  num_nodes = models.PositiveIntegerField()
  num_threads = models.PositiveSmallIntegerField()
  num_gpus = models.PositiveIntegerField()

  queue = models.CharField(max_length=64)
  user = models.CharField(max_length=32)
  exec_path = models.CharField(max_length=1024)
  sum_runs = models.PositiveIntegerField()
  sum_time = models.FloatField()

  module_name = models.CharField(max_length=64, null=True)
  cwd = models.CharField(max_length=1024)
  cmdline = models.BinaryField()
  container = models.CharField(max_length=32, null=True)

  def __str__(self) -> Any:
    """
    Return string representation (run_id).
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    return str(self.run_id)


class join_run_object(models.Model):
  """
  Links run_id to obj_id (lib). Table: join_run_object.
  """
  join_id = models.PositiveIntegerField(primary_key=True)
  obj_id = models.PositiveIntegerField()
  run_id = models.PositiveIntegerField()
  date = models.DateField()

  class Meta:
    """
    Django model metadata for the enclosing model.
    """
    managed = False
    db_table = "join_run_object"

  def __str__(self) -> Any:
    """
    Return string representation (run_id).
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    return str(self.run_id)


class lib(models.Model):
  """
  XALT library/object record: object_path, module_name, etc. Table: xalt_object.
  """
  # Mirrors upstream XALT MySQL schema (see upstream `py_src/createDB.in.py`).
  class Meta:
    """
    Django model metadata for the enclosing model.
    """
    managed = False
    db_table = "xalt_object"

  obj_id = models.PositiveIntegerField(primary_key=True)
  object_path = models.CharField(max_length=1024)
  syshost = models.CharField(max_length=64)
  hash_id = models.CharField(max_length=40)
  module_name = models.CharField(max_length=64, null=True)
  timestamp = models.DateTimeField(null=True)
  lib_type = models.CharField(max_length=2)

  def __str__(self) -> Any:
    """
    Return string representation (obj_id).
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    return str(self.obj_id)


class join_link_object(models.Model):
  """
  Links obj_id to link_id. Table: join_link_object.
  """

  class Meta:
    """
    Django model metadata for the enclosing model.
    """
    managed = False
    db_table = "join_link_object"

  join_id = models.PositiveIntegerField(primary_key=True)
  obj_id = models.PositiveIntegerField()
  link_id = models.PositiveIntegerField()
  date = models.DateField()

  def __str__(self) -> Any:
    """
    Return string representation (join_id).
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    return str(self.join_id)


class link(models.Model):
  """
  XALT link record. Table: link.
  """
  # Mirrors upstream XALT MySQL schema (see upstream `py_src/createDB.in.py`).
  class Meta:
    """
    Django model metadata for the enclosing model.
    """
    managed = False
    db_table = "xalt_link"

  link_id = models.PositiveIntegerField(primary_key=True)
  uuid = models.CharField(max_length=36)
  hash_id = models.CharField(max_length=40)
  date = models.DateTimeField()
  link_program = models.CharField(max_length=64)
  link_path = models.CharField(max_length=1024)
  link_module_name = models.CharField(max_length=64, null=True)
  link_line = models.BinaryField()
  cwd = models.CharField(max_length=1024, null=True)
  build_user = models.CharField(max_length=64)
  build_syshost = models.CharField(max_length=64)
  build_epoch = models.FloatField()
  exec_path = models.CharField(max_length=1024)

  def __str__(self) -> Any:
    """
    Return string representation (link_id).
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    return str(self.link_id)
