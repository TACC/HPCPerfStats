# Generated manually for public dashboard gzip artifacts.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0023_reduce_host_data_compression_to_8_days"),
    ]

    operations = [
        migrations.CreateModel(
            name="public_metrics_artifact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("scope", models.CharField(db_index=True, max_length=64)),
                ("period_key", models.CharField(db_index=True, max_length=64)),
                ("payload_compressed", models.BinaryField()),
                ("payload_encoding", models.CharField(max_length=32)),
                ("input_fingerprint", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "public_metrics_artifact",
            },
        ),
        migrations.AddConstraint(
            model_name="public_metrics_artifact",
            constraint=models.UniqueConstraint(
                fields=("scope", "period_key"),
                name="public_metrics_artifact_scope_period_uniq",
            ),
        ),
    ]
