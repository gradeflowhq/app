from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("live", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="topic", old_name="changed", new_name="changed_at"),
    ]
