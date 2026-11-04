import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0001_initial"),
        ("imports", "0002_rename_student_fields"),
    ]

    operations = [
        migrations.RenameModel(old_name="ResultsFile", new_name="SubmissionsFile"),
        migrations.AlterField(
            model_name="submissionsfile",
            name="assessment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="submissions_files", to="assessments.assessment"
            ),
        ),
    ]
