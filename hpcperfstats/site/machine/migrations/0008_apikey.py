# API key table: key -> username mapping for programmatic access.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0007_metrics_data_metric_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiKey",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("username", models.CharField(db_index=True, max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "api_keys",
            },
        ),
    ]
