from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("exports", "0002_rename_publication_and_canvas_group"),
    ]

    operations = [
        migrations.RemoveField(model_name="coursecanvas", name="canvas_assignment_group"),
        migrations.RemoveField(model_name="coursecanvas", name="canvas_assignment_group_name"),
    ]
