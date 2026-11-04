import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0001_initial"),
        ("exports", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="Publication", new_name="SentMarks"),
        migrations.AlterField(
            model_name="sentmarks",
            name="assessment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="sent_marks", to="assessments.assessment"
            ),
        ),
        migrations.RenameField(model_name="coursecanvas", old_name="canvas_group", new_name="canvas_assignment_group"),
        migrations.RenameField(
            model_name="coursecanvas", old_name="canvas_group_name", new_name="canvas_assignment_group_name"
        ),
    ]
