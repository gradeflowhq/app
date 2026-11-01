from django.apps import AppConfig


class Config(AppConfig):
    name = "gradeflow_app.marking"
    label = "marking"

    def ready(self) -> None:
        from gradeflow_app.marking import previews, remarks  # noqa: F401 - register their jobs
        from gradeflow_app.marking.answers import comparisons  # noqa: F401 - registers its job
