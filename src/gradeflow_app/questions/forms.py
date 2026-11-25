"""A question's settings, as the core's schema of its type describes them."""

from collections.abc import Iterable, Mapping
from typing import Any

from gradeflow_core import InvalidError, Question
from gradeflow_core.editing import QUESTION_CLASSES, QuestionType, converted_question

from gradeflow_app.questions.models import QUESTION_TYPE_LABELS
from gradeflow_app.ui.schemaforms.forms import ObjectForm, with_row_edit


class QuestionForm(ObjectForm):
    def __init__(self, question_type: QuestionType, values: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(QUESTION_CLASSES[question_type].json_schema(), values, **kwargs)
        self.question_type = question_type

    def posted_question(self) -> Question | None:
        """The question as posted; None, with the core's problems on the form, if it isn't one."""
        if not self.is_valid():
            return None
        try:
            return QUESTION_CLASSES[self.question_type].load(self.values())
        except InvalidError as error:
            self.add_problems(error.problems)
            return None


def question_form(question: Question, data: Mapping[str, Any]) -> QuestionForm:
    """The form of `question`: bound to `data` when it saves, else a new one with the row `data` adds or removes, if
    any."""
    values = question.model_dump(mode="json")
    if "save" in data:
        return QuestionForm(question.type, values, data=data)
    posted = QuestionForm(question.type, values, data=data)
    posted.full_clean()
    edited_values = with_row_edit(posted, posted.values(), data)
    return QuestionForm(question.type, values if edited_values is None else edited_values)


def question_type_named(value: str, default: QuestionType) -> QuestionType:
    """The question type `value` names, or `default` if it names none."""
    return value if value in QUESTION_TYPE_LABELS else default


def retyped(current: Question, question_type: QuestionType, answers: Iterable[str]) -> Question:
    """`current` as a question of `question_type`: set up from its `answers` if it is of another type."""
    return current if current.type == question_type else converted_question(current, question_type, list(answers))
