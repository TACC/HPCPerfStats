"""Add singleton TestLoginUser for the INI-gated hidden test-login surface."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0032_host_data_unique_include_dev_db"),
    ]

    operations = [
        migrations.CreateModel(
            name="TestLoginUser",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name="ID",
                )),
                ("username", models.CharField(max_length=128)),
                ("password_hash", models.CharField(max_length=256)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.CharField(max_length=128)),
            ],
            options={
                "db_table": "test_login_user",
            },
        ),
    ]
