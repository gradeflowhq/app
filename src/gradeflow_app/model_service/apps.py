from django.apps import AppConfig


class Config(AppConfig):
    name = "gradeflow_app.model_service"
    label = "model_service"

    def ready(self) -> None:
        from gradeflow_app.model_service import choosing  # noqa: F401 - registers its job
