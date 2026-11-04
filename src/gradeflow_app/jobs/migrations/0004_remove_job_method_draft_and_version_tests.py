from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0003_rename_batch_and_job_fields"),
    ]

    operations = [
        migrations.RemoveField(model_name="job", name="method_draft"),
        migrations.RemoveField(model_name="job", name="version_tests"),
    ]
