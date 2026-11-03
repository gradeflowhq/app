from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0002_result_breakdown"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="result", name="one_result_per_question"),
        migrations.AddConstraint(
            model_name="result",
            constraint=models.UniqueConstraint(fields=("student", "question"), name="one_result_per_student_question"),
        ),
        migrations.RemoveConstraint(model_name="adjustment", name="one_adjustment_per_question"),
        migrations.AddConstraint(
            model_name="adjustment",
            constraint=models.UniqueConstraint(
                fields=("student", "question"), name="one_adjustment_per_student_question"
            ),
        ),
    ]
