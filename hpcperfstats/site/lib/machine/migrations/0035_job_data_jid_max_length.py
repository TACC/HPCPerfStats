"""Widen job_data.jid so heterogeneous sacct JobIDs fit the primary key.

Django AlterField emits ``USING "jid"::varchar(512)`` on the referencing
foreign keys because their rendered types differ (varchar(32) vs
varchar(512)). PostgreSQL rewrites the table when USING is present.
These statements change only the typmod.
"""

from django.db import migrations, models

# Names from CreateModel / AlterField suffix ``_fk_%(to_table)s_%(to_column)s``.
_FK_TABLES = (
    ("metrics_data", "metrics_data_jid_d251aeb6_fk_job_data_jid"),
    ("job_plot_artifact", "job_plot_artifact_jid_2614497c_fk_job_data_jid"),
    ("job_detail_artifact", "job_detail_artifact_jid_072a4361_fk_job_data_jid"),
)


def _typmod_sql(varchar_type):
    drop = [
        f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'
        for table, name in _FK_TABLES
    ]
    alter = [
        f'ALTER TABLE "{table}" ALTER COLUMN "jid" TYPE {varchar_type}'
        for table in (
            "job_data",
            "metrics_data",
            "job_plot_artifact",
            "job_detail_artifact",
        )
    ]
    add = [
        (
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("jid") REFERENCES "job_data" ("jid") '
            f"DEFERRABLE INITIALLY DEFERRED"
        )
        for table, name in _FK_TABLES
    ]
    return drop + alter + add


class Migration(migrations.Migration):

    dependencies = [
        ("machine", "0034_metrics_host_data_sql_functions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="job_data",
                    name="jid",
                    field=models.CharField(
                        max_length=512, primary_key=True, serialize=False
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=_typmod_sql("varchar(512)"),
                    reverse_sql=_typmod_sql("varchar(32)"),
                ),
            ],
        ),
    ]
