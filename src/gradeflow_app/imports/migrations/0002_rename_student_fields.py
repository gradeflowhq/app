from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="student", name="one_student_per_key"),
        migrations.RenameField(model_name="student", old_name="key", new_name="encrypted_id"),
        migrations.RenameField(model_name="student", old_name="file_marks", new_name="imported_marks"),
        migrations.AddConstraint(
            model_name="student",
            constraint=models.UniqueConstraint(
                fields=("assessment", "encrypted_id"), name="one_student_per_encrypted_id"
            ),
        ),
    ]
