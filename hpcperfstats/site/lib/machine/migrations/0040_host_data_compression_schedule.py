"""Run the host_data compression policy once a day at 01:00 local time.

See ``hpcperfstats.site.lib.machine.compression_schedule`` for the why and the
SQL. Reverse restores the floating 12 hour schedule.
"""

from django.db import migrations

from hpcperfstats.site.lib.machine.compression_schedule import (
  pin_compression_schedule,
  restore_compression_schedule,
)


class Migration(migrations.Migration):
  dependencies = [
    ("machine", "0039_jid_scoped_distinct_host_filter"),
  ]

  operations = [
    migrations.RunPython(
      pin_compression_schedule,
      restore_compression_schedule,
    ),
  ]
