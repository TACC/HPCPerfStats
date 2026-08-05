"""
apps.
"""

from __future__ import annotations

from django.apps import AppConfig


class MachineConfig(AppConfig):
  """
  Hold MachineConfig state and behavior.
  
  Subclasses ``AppConfig``, extending that type with this class's fields and
  behavior.
  
  Subclasses ``AppConfig``, extending that type with this class's fields and
  behavior.
  """
  default_auto_field = "django.db.models.BigAutoField"
  name = "hpcperfstats.site.lib.machine"
  label = "machine"
