"""Persist gzip-compressed Bokeh json_item per job plot kind and layout."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
  dependencies = [
      ("machine", "0016_apikey_hash_storage"),
  ]

  operations = [
      migrations.CreateModel(
          name="job_plot_artifact",
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
              ("plot_kind", models.CharField(max_length=32)),
              ("layout", models.CharField(max_length=16)),
              ("payload_compressed", models.BinaryField()),
              ("payload_encoding", models.CharField(max_length=32)),
              ("input_fingerprint", models.CharField(max_length=64)),
              ("created_at", models.DateTimeField(auto_now_add=True)),
              ("updated_at", models.DateTimeField(auto_now=True)),
              (
                  "jid",
                  models.ForeignKey(
                      db_column="jid",
                      on_delete=django.db.models.deletion.CASCADE,
                      related_name="plot_artifacts",
                      to="machine.job_data",
                  ),
              ),
          ],
          options={
              "db_table": "job_plot_artifact",
          },
      ),
      migrations.AddConstraint(
          model_name="job_plot_artifact",
          constraint=models.UniqueConstraint(
              fields=("jid", "plot_kind", "layout"),
              name="job_plot_artifact_jid_kind_layout_uniq",
          ),
      ),
  ]
