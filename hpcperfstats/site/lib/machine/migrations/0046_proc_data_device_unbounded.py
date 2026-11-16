"""Make ``proc_data.device`` an unbounded ``varchar``.

``device`` is the monitor's full ``name/pid/Cpus_allowed_list/Mems_allowed_list``
token. ``varchar(512)`` forced the monitor to cut it at 511 bytes, losing the
tail of long CPU lists on large nodes. Dropping a ``varchar`` length limit is
binary coercible, so PostgreSQL only updates the catalog (no table rewrite, no
index rebuild). Migrate runs before gunicorn and the pipeline waits for
gunicorn, so nothing else holds ``proc_data`` locks while this runs.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
    ("machine", "0045_drop_duplicate_indexes"),
  ]

  operations = [
    migrations.AlterField(
      model_name="proc_data",
      name="device",
      field=models.CharField(blank=True, max_length=None, null=True),
    ),
  ]
