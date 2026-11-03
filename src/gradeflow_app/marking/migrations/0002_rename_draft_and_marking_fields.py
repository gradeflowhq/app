from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("marking", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="Draft", new_name="MarkingDraftRecord"),
        migrations.RenameField(
            model_name="markingdraftrecord", old_name="based_on", new_name="saved_marking_updated_at"
        ),
        migrations.RenameField(model_name="markingdraftrecord", old_name="previewed", new_name="preview_input"),
        migrations.RenameField(model_name="marking", old_name="uses", new_name="used_versions"),
        migrations.RenameField(model_name="marking", old_name="output_keys", new_name="questions_marked"),
        migrations.RenameField(model_name="marking", old_name="input_keys", new_name="questions_read"),
        migrations.RenameField(model_name="historicalmarking", old_name="output_keys", new_name="questions_marked"),
        migrations.RenameField(model_name="historicalmarking", old_name="input_keys", new_name="questions_read"),
    ]
