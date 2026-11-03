from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0002_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="Batch", new_name="SubmissionBatch"),
        migrations.RenameField(model_name="job", old_name="core_build", new_name="core_version"),
        migrations.RenameField(model_name="job", old_name="provenance", new_name="marked_with"),
        migrations.RenameField(model_name="job", old_name="questions", new_name="question_keys"),
        migrations.RenameField(model_name="job", old_name="step", new_name="progress_text"),
        migrations.RenameField(model_name="job", old_name="admitted_at", new_name="dispatched_at"),
        migrations.AlterField(
            model_name="job",
            name="kind",
            field=models.CharField(
                choices=[
                    ("remark", "Remark"),
                    ("preview", "Preview"),
                    ("grouping", "Grouping"),
                    ("draft_tests", "Draft Tests"),
                    ("version_tests", "Version Tests"),
                    ("model_choice", "Model Choice"),
                    ("model_trial", "Model Trial"),
                ],
                max_length=20,
            ),
        ),
    ]
