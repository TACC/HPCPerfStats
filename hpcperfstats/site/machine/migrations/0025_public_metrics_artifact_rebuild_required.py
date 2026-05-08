# Mark-and-rebuild for /pub EF histograms (soft invalidation).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0024_public_metrics_artifact"),
    ]

    operations = [
        migrations.AddField(
            model_name="public_metrics_artifact",
            name="rebuild_required",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
