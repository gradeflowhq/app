from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0004_result_method_text"),
    ]

    operations = [
        migrations.RenameField(model_name="result", old_name="method", new_name="method_label"),
        migrations.RenameField(model_name="result", old_name="marked", new_name="is_marked"),
        migrations.RenameField(model_name="result", old_name="language_models", new_name="model_names"),
    ]
