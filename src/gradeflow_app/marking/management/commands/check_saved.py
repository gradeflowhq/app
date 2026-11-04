"""Lists what is saved that the installed core can't load, to run on a copy of the database before deploying a core
that reads saved data differently."""

from typing import Any

from django.core.management.base import BaseCommand
from gradeflow_core import BUILTINS, Check, GradeFlowError
from gradeflow_core.editing import load_method_files, methods_used

from gradeflow_app.libraries.collection import files_needed_by, method_files
from gradeflow_app.libraries.models import MethodVersion
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import Marking, MarkingDraftRecord


class Command(BaseCommand):
    help = "List the saved method versions, markings and marking drafts the installed core can't load."

    def handle(self, *args: Any, **options: Any) -> None:
        failures = load_method_files(method_files(MethodVersion.objects.all()), BUILTINS).failed
        problems = [
            f"{ref}: {' '.join(file.messages)}" if file.ref == ref else f"{ref}: uses {file.ref}"
            for ref, file in failures.items()
        ]
        markings = Marking.objects.select_related("assessment__course", "question")
        drafts = MarkingDraftRecord.objects.select_related("assessment__course", "question", "user")
        records = [(marking, str(marking)) for marking in markings] + [
            (draft, f"{draft.question.key if draft.question else 'Question group'}, draft by {draft.user}")
            for draft in drafts
        ]
        for record, name in records:
            if error := _load_error(record.definition):
                problems.append(f"{record.assessment.course} › {record.assessment} › {name}: {error}")
        self.stdout.write("\n".join(problems) or "Everything loads.")


def _load_error(definition: str) -> str | None:
    data = definition_data(definition)
    try:
        Check.load(data, methods=load_method_files(files_needed_by(methods_used(data)), BUILTINS).methods)
    except GradeFlowError as error:
        return " ".join(str(error).splitlines())
    return None
