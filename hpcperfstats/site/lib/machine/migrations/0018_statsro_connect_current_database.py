"""Grant statsro CONNECT on the database being migrated.

0002 uses cfg.get_db_name() for GRANT CONNECT (INI [PORTAL] name). While
migrating test_hpcperfstats that targets the primary dbname, not the test DB.
Idempotent CONNECT grant for connection.settings_dict['NAME'].
"""
from django.db import migrations


def _grant_statsro_connect_current_database(apps, schema_editor):
  conn = schema_editor.connection
  db_name = conn.settings_dict["NAME"]
  safe_db = db_name.replace('"', '""')
  with conn.cursor() as cursor:
    cursor.execute(f'GRANT CONNECT ON DATABASE "{safe_db}" TO statsro')


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0017_job_plot_artifact"),
  ]

  operations = [
      migrations.RunPython(
          _grant_statsro_connect_current_database,
          migrations.RunPython.noop,
      ),
  ]
