"""Reduce TimescaleDB host_data compression window to 8 days."""

from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0022_job_detail_artifact"),
  ]

  operations = [
      migrations.RunSQL(
          "SELECT remove_compression_policy('host_data');"
          "SELECT add_compression_policy('host_data', compress_after => INTERVAL '8d');",
          "SELECT remove_compression_policy('host_data');"
          "SELECT add_compression_policy('host_data', compress_after => INTERVAL '30d');",
      ),
  ]
