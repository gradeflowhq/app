from django.apps import AppConfig


class Config(AppConfig):
    name = "gradeflow_app.libraries"
    label = "libraries"

    def ready(self) -> None:
        from gradeflow_app.libraries import owners  # noqa: F401 - connects its receivers
        from gradeflow_app.libraries.designer import runs  # noqa: F401 - registers its jobs
