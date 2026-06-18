from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0008_apikey"),
    ]

    operations = [
        migrations.AddField(
            model_name="apikey",
            name="is_staff",
            field=models.BooleanField(default=False),
        ),
    ]

