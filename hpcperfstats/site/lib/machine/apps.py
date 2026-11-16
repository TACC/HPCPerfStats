"""
Django app configuration for the machine app.
"""

from __future__ import annotations

from django.apps import AppConfig


class MachineConfig(AppConfig):
  """
  Register the machine Django app under the stable ``machine`` label.

  Subclasses ``AppConfig``, which Django uses to configure an installed app;
  this class sets the app path, label, and default auto field.
  """

  default_auto_field = "django.db.models.BigAutoField"
  name = "hpcperfstats.site.lib.machine"
  label = "machine"
