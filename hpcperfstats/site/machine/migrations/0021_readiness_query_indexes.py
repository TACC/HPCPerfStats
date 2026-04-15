from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0020_host_data_primary_key_contract"),
  ]

  operations = [
      migrations.AddIndex(
          model_name="host_data",
          index=models.Index(
              fields=["host", "-time"],
              name="host_data_host_time_desc_idx",
          ),
      ),
      migrations.AddIndex(
          model_name="host_data",
          index=models.Index(
              fields=["jid", "-time"],
              name="host_data_jid_time_desc_idx",
          ),
      ),
      migrations.AddIndex(
          model_name="metrics_data",
          index=models.Index(
              fields=["jid"],
              name="metrics_data_stale_jid_idx",
              condition=Q(value__isnull=True)
              & (Q(no_data_reason__isnull=True) | Q(no_data_reason="")),
          ),
      ),
  ]
