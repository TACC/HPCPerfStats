#!/usr/bin/env python
"""Django management script: sets DJANGO_SETTINGS_MODULE and runs execute_from_command_line.

AI generated.
"""
import os
import sys

if __name__ == "__main__":
  os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                        "hpcperfstats.site.hpcperfstats_site.settings")

  from django.core.management import execute_from_command_line

  execute_from_command_line(sys.argv)
