import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="kdf_salt",
            field=models.CharField(default="", max_length=24),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="user",
            name="wrapped_private_key",
            field=models.CharField(default="", max_length=104),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="user",
            name="public_key",
            field=models.CharField(default="", max_length=44),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name="Secret",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(choices=[("canvas", "Canvas"), ("ai-service", "Ai Service")], max_length=20)),
                ("encrypted_value", models.TextField()),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="secrets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("user", "name"), name="one_secret_per_user_name")],
            },
        ),
    ]
