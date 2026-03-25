import hashlib

from django.db import migrations, models


def migrate_apikeys_to_hashed(apps, schema_editor):
    ApiKey = apps.get_model("machine", "ApiKey")
    for api_key in ApiKey.objects.all().iterator():
        # `key` is the primary key column; Django forbids including primary
        # keys in `save(update_fields=[...])`. We also avoid double-hashing
        # on re-runs by skipping rows where the new `key_prefix` is already
        # populated.
        if getattr(api_key, "key_prefix", ""):
            continue

        raw_value = api_key.key or ""
        api_key.key_prefix = raw_value[:12]
        api_key.key = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()

        # No `update_fields`: we must persist PK changes too.
        api_key.save()


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0015_job_data_metrics_distinct_time_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="apikey",
            name="key_prefix",
            field=models.CharField(db_index=True, default="", max_length=12),
        ),
        migrations.RunPython(migrate_apikeys_to_hashed, migrations.RunPython.noop),
    ]
