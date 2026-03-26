"""Update TimescaleDB compression policy for host_data.

This migration adjusts the compress_after interval to better match
typical query windows (recent jobs), keeping recent chunks
uncompressed for fast access and compressing older data sooner.
"""

from django.db import migrations


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0011_composite_indexes"),
  ]

  operations = [
      migrations.RunSQL(
          # New policy: compress chunks older than 30 days
          # TimescaleDB does not provide alter_compression_policy() on all versions,
          # so we remove the existing policy (created in 0001_initial) and add a new one.
          "SELECT remove_compression_policy('host_data');"
          "SELECT add_compression_policy('host_data', compress_after => INTERVAL '30d');",
          # Reverse: restore original 60-day compression window
          "SELECT remove_compression_policy('host_data');"
          "SELECT add_compression_policy('host_data', compress_after => INTERVAL '60d');",
      ),
  ]

