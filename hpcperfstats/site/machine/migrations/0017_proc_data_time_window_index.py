from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0016_apikey_hash_storage"),
    ]

    operations = [
        migrations.AddField(
            model_name="proc_data",
            name="time",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterUniqueTogether(
            name="proc_data",
            unique_together={("jid", "host", "proc", "time")},
        ),
        migrations.AddIndex(
            model_name="proc_data",
            index=models.Index(fields=["host", "time"], name="machine_proc_host_98fdd2_idx"),
        ),
    ]
