"""XALT database models: run, join_run_object, lib. Used for executable path and library info per job (read via views).

AI generated.
"""
from django.db import models


class run(models.Model):
  """XALT run record: job_id, exec_path, cwd, times, user, etc.

    AI generated.
    """
  run_id = models.BigIntegerField(primary_key=True)
  job_id = models.CharField(max_length=11)
  run_uuid = models.CharField(max_length=36)

  date = models.DateTimeField()
  syshost = models.CharField(max_length=64)
  uuid = models.CharField(max_length=36, null=True)
  hash_id = models.CharField(max_length=40)

  account = models.CharField(max_length=11)
  exec_type = models.CharField(max_length=7)
  start_time = models.FloatField()
  end_time = models.FloatField()
  run_time = models.FloatField()

  num_cores = models.PositiveIntegerField()
  num_nodes = models.PositiveIntegerField()
  num_threads = models.PositiveIntegerField()

  queue = models.CharField(max_length=32)
  user = models.CharField(max_length=32)
  exec_path = models.CharField(max_length=1024)

  module_name = models.CharField(max_length=64, null=True)
  cwd = models.CharField(max_length=1024, null=True)

  def __unicode__(self):
    """Return string representation (run_id).

        AI generated.
        """
    return str(self.run_id)


class join_run_object(models.Model):
  """Links run_id to obj_id (lib). Table: join_run_object.

    AI generated.
    """
  join_id = models.PositiveIntegerField(primary_key=True)
  obj_id = models.PositiveIntegerField()
  run_id = models.PositiveIntegerField()

  class Meta:
    db_table = "join_run_object"

  def __unicode__(self):
    """Return string representation (run_id).

        AI generated.
        """
    return str(self.run_id)


class lib(models.Model):
  """XALT library/object record: object_path, module_name, etc. Table: xalt_object.

    AI generated.
    """
  obj_id = models.PositiveIntegerField(primary_key=True)
  object_path = models.CharField(max_length=1024)
  syshost = models.CharField(max_length=64)
  hash_id = models.CharField(max_length=40)
  module_name = models.CharField(max_length=64)
  timestamp = models.FloatField()
  lib_type = models.CharField(max_length=2)

  class Meta:
    db_table = "xalt_object"

  def __unicode__(self):
    """Return string representation (obj_id).

        AI generated.
        """
    return str(self.obj_id)


class join_link_object(models.Model):
  """Links obj_id to link_id. Table: join_link_object.

    AI generated.
    """

  class Meta:
    db_table = "join_link_object"

  join_id = models.PositiveIntegerField(primary_key=True)
  obj_id = models.PositiveIntegerField()
  link_id = models.PositiveIntegerField()

  def __unicode__(self):
    """Return string representation (join_id).

        AI generated.
        """
    return str(self.join_id)


class link(models.Model):
  """XALT link record. Table: link.

    AI generated.
    """
  link_id = models.PositiveIntegerField(primary_key=True)
  uuid = models.CharField(max_length=36)
  hash_id = models.CharField(max_length=40)
  date = models.DateTimeField(null=True)
  link_program = models.CharField(max_length=10)
  build_user = models.CharField(max_length=64)
  build_syshost = models.CharField(max_length=64)
  build_epoch = models.FloatField()
  exit_code = models.IntegerField()
  exec_path = models.CharField(max_length=1024)

  def __unicode__(self):
    """Return string representation (link_id).

        AI generated.
        """
    return str(self.link_id)
