from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="member", name="one_member_per_user"),
        migrations.AddConstraint(
            model_name="member",
            constraint=models.UniqueConstraint(fields=("course", "user"), name="one_member_per_course_user"),
        ),
    ]
