from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("libraries", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="MethodDraft", new_name="MethodDraftRecord"),
        migrations.RenameField(model_name="methoddraftrecord", old_name="run", new_name="last_run"),
        migrations.RenameField(model_name="versiontests", old_name="run", new_name="last_run"),
        migrations.RenameField(model_name="method", old_name="next_version", new_name="next_version_number"),
        migrations.RenameField(model_name="methodversion", old_name="uses", new_name="used_versions"),
        migrations.RemoveConstraint(model_name="versionuse", name="one_version_use"),
        migrations.RenameField(model_name="versionuse", old_name="used", new_name="used_version"),
        migrations.AddConstraint(
            model_name="versionuse",
            constraint=models.UniqueConstraint(fields=("version", "used_version"), name="one_version_use"),
        ),
    ]
