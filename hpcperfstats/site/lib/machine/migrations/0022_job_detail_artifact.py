from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

  dependencies = [
      ("machine", "0021_readiness_query_indexes"),
  ]

  operations = [
      migrations.CreateModel(
          name="job_detail_artifact",
          fields=[
              ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
              ("artifact_kind", models.CharField(max_length=32)),
              ("artifact_scope", models.CharField(default="", max_length=128)),
              ("payload_compressed", models.BinaryField()),
              ("payload_encoding", models.CharField(max_length=32)),
              ("input_fingerprint", models.CharField(max_length=64)),
              ("created_at", models.DateTimeField(auto_now_add=True)),
              ("updated_at", models.DateTimeField(auto_now=True)),
              ("jid", models.ForeignKey(db_column="jid", on_delete=django.db.models.deletion.CASCADE, related_name="detail_artifacts", to="machine.job_data")),
          ],
          options={
              "db_table": "job_detail_artifact",
              "managed": True,
          },
      ),
      migrations.AddConstraint(
          model_name="job_detail_artifact",
          constraint=models.UniqueConstraint(fields=("jid", "artifact_kind", "artifact_scope"), name="job_detail_artifact_jid_kind_scope_uniq"),
      ),
  ]
