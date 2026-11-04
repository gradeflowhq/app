"""Stored markings as the core reads them."""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from gradeflow_core import Check, GradeFlowError, Methods, QuestionSet
from gradeflow_core.editing import MarkingDraft, Position, compatible_methods, load_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.libraries.collection import course_methods, usable

if TYPE_CHECKING:
    from gradeflow_app.marking.models import Marking  # which saves through `definition_data`


def definition_data(definition: str) -> dict[str, Any]:
    try:
        tree = load_yaml(definition)
    except GradeFlowError:
        return {}
    return tree if isinstance(tree, dict) else {}


def check_of(definition: str, methods: Methods) -> Check | None:
    try:
        return Check.load(definition_data(definition), methods=methods)
    except GradeFlowError:
        return None


def marking_draft(definition: str, questions: QuestionSet, key: str | None, methods: Methods) -> MarkingDraft:
    """A stored marking of the question `key`, or of a question group, as the core edits it: where each of its
    problems is."""
    return MarkingDraft(definition_data(definition), questions, key, methods=methods)


def accepted_checks(markings: Iterable[Marking], methods: Methods, questions: QuestionSet) -> dict[Marking, Check]:
    """Each of an assessment's `markings` the core accepts, with its check. Those it no longer accepts mark nothing
    until fixed."""
    return {
        marking: check
        for marking in markings
        if (check := check_of(marking.definition, methods)) is not None and not check.problems(questions)
    }


def assessment_checks(assessment: Assessment) -> dict[Marking, Check]:
    """Each of the assessment's markings the core accepts, with its check."""
    markings = assessment.markings.select_related("question")
    return accepted_checks(markings, course_methods(assessment.course), assessment.question_set())


def check_for(marking: Marking) -> Check | None:
    """The marking's check, while the core accepts it."""
    assessment = marking.assessment
    return accepted_checks([marking], course_methods(assessment.course), assessment.question_set()).get(marking)


def methods_for(user: User, assessment: Assessment, key: str | None = None) -> Methods:
    """The methods `user` may mark the question `key` with, or else a question group."""
    position = Position(assessment.question_set(), key, in_group=key is None)
    return compatible_methods(position, methods=usable(user, assessment.course))


def questions_marked(marking: Marking, check: Check | None) -> set[str]:
    """The questions `marking` gives marks to, as its accepted `check` says; while it has none, those it marked when
    it last could, which stay its own."""
    return check.questions_marked() if check is not None else marking.questions_given_marks


def questions_involved(marking: Marking, check: Check | None) -> set[str]:
    """The questions `marking` marks or reads, likewise."""
    if check is not None:
        return check.questions_marked() | check.questions_read()
    return marking.questions_given_marks | set(marking.questions_read)
