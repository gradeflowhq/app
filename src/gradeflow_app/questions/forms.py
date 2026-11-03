"""A question's settings, as the core's schema of its type describes them."""

from collections.abc import Iterable
from typing import Any, cast

from gradeflow_core import InvalidError, Question
from gradeflow_core.editing import QUESTION_TYPES, QuestionType, converted_question, new_question

from gradeflow_app.ui.schemaforms.forms import ObjectForm


class QuestionForm(ObjectForm):
    def __init__(self, question: Question, **kwargs: Any) -> None:
        super().__init__(question.json_schema(), question.model_dump(mode="json"), **kwargs)
        self.question_type = question.type

    def posted_question(self) -> Question | None:
        """The question as posted; None, with the core's problems on the form, if it isn't one."""
        if not self.is_valid():
            return None
        try:
            return new_question(self.question_type, self.values())
        except InvalidError as error:
            self.add_problems(error.problems)
            return None


def question_type_named(value: str, default: QuestionType) -> QuestionType:
    """The question type `value` names, or `default` if it names none."""
    return cast(QuestionType, value) if value in QUESTION_TYPES else default


def retyped(current: Question, question_type: QuestionType, answers: Iterable[str]) -> Question:
    """`current` as a question of `question_type`: set up from its `answers` if it is of another type."""
    return current if current.type == question_type else converted_question(current, question_type, list(answers))
