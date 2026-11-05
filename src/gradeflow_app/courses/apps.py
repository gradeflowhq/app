from django.apps import AppConfig


class Config(AppConfig):
    name = "gradeflow_app.courses"
    label = "courses"

    def ready(self) -> None:
        from gradeflow_app.courses import invitations, keys  # noqa: F401 - connect their receivers
