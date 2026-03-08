# Add indexes for efficient filters, order_by, and distinct on job_data and host_data.
# job_data: username, account, queue, state (filters + distinct), start_time (order_by).
# host_data: composite (jid, type, event, time) for detail queries.
from django.db import migrations, models


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0005_remove_unused_indexes"),
  ]

  operations = [
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(fields=["username"], name="job_data_username_idx"),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(fields=["account"], name="job_data_account_idx"),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(fields=["queue"], name="job_data_queue_idx"),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(fields=["state"], name="job_data_state_idx"),
      ),
      migrations.AddIndex(
          model_name="job_data",
          index=models.Index(fields=["start_time"], name="job_data_start_time_idx"),
      ),
      migrations.AddIndex(
          model_name="host_data",
          index=models.Index(
              fields=["jid", "type", "event", "time"],
              name="host_data_jid_type_ev_time_idx",
          ),
      ),
  ]
