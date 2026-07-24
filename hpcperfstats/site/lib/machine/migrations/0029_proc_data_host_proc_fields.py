from django.db import migrations, models


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0028_artifact_schema"),
  ]

  operations = [
      migrations.AddField(
          model_name="proc_data",
          name="device",
          field=models.CharField(blank=True, max_length=512, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="uid",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_peak",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_size",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_lck",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_hwm",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_rss",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_data",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_stk",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_exe",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_lib",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_pte",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="vm_swap",
          field=models.BigIntegerField(blank=True, null=True),
      ),
      migrations.AddField(
          model_name="proc_data",
          name="threads",
          field=models.IntegerField(blank=True, null=True),
      ),
  ]
