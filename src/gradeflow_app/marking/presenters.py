"""What the marking pages show, for their templates: the question list and the marking column."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from django.urls import reverse
from gradeflow_core import Check, GradeFlowError, Methods, QuestionSet
from gradeflow_core.editing import MarkingDraft

from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.editor.editing import located
from gradeflow_app.editor.guide import Choices, choices
from gradeflow_app.imports.models import has_imported_marks, imported_mark_keys
from gradeflow_app.libraries.collection import OfferedMethods, course_methods, offers, usable
from gradeflow_app.libraries.models import Method
from gradeflow_app.marking.checks import (
    assessment_checks,
    marking_draft,
    methods_for,
    questions_involved,
    questions_marked,
)
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.remarks import running_remarks
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS, Question
from gradeflow_app.results.marks import average_proportions


@dataclass(frozen=True)
class MarkingSummary:
    """A marking as the core describes it, or, while it can't mark, what stops it."""

    method_label: str
    description: str
    problems: tuple[str, ...] = ()

    @property
    def has_problems(self) -> bool:
        return bool(self.problems)


def summary_of(check: Check) -> MarkingSummary:
    """A marking the core accepts."""
    try:
        return MarkingSummary(check.method.label, check.describe())
    except GradeFlowError as error:
        return MarkingSummary(check.method.label, "", (str(error),))


def saved_summary(definition: str, key: str | None, methods: Methods, questions: QuestionSet) -> MarkingSummary:
    """A saved marking of the question `key`, or of a question group, which can stop marking when the core or its
    questions change."""
    draft = marking_draft(definition, questions, key, methods)
    if problems := draft.problems:
        method = draft.method("")
        problem_texts = tuple(dict.fromkeys(located(draft, problem) for problem in problems))
        return MarkingSummary(method.label if method else "", "", problem_texts)
    return summary_of(draft.finalized())


def _summary(marking: Marking, check: Check | None, assessment: Assessment) -> MarkingSummary:
    """`marking` as `check`, the core's reading of it, while it can mark."""
    if check is not None:
        return summary_of(check)
    key = marking.question.key if marking.question else None
    return saved_summary(marking.definition, key, course_methods(assessment.course), assessment.question_set())


@dataclass(frozen=True)
class MarkingBadge:
    """How a question is marked, in a word or two: by a method, by its imported marks, or not until it's marked or
    fixed."""

    label: str
    style: Literal["method", "imported", "warning"]


def marking_badge(method_label: str | None, *, has_problems: bool, has_imported_marks: bool) -> MarkingBadge:
    """The badge of a question marked with the method `method_label`, or else one whose marking can't mark, or one
    with only its imported marks."""
    if method_label:
        return MarkingBadge(method_label, "method")
    if has_problems:
        return MarkingBadge("Can't mark", "warning")
    if has_imported_marks:
        return MarkingBadge("Imported marks", "imported")
    return MarkingBadge("Not marked", "warning")


@dataclass(frozen=True)
class SavedMarking:
    """A question's saved marking: its own, or the question group's that marks it."""

    marking: Marking
    method_label: str | None
    """The label of its method, while it can mark."""

    @property
    def has_problems(self) -> bool:
        return self.method_label is None

    @property
    def in_question_group(self) -> bool:
        return self.marking.question_id is None


def saved_markings(markings: Iterable[Marking], accepted: Mapping[Marking, Check]) -> dict[str, SavedMarking]:
    """The saved marking of each question that has one, by key."""
    # A question's own marking comes last, so it wins over a question group that still lists it.
    ordered = sorted(markings, key=lambda marking: marking.question_id is not None)
    by_key = {key: marking for marking in ordered for key in questions_marked(marking, accepted.get(marking))}
    return {
        key: SavedMarking(marking, accepted[marking].method.label if marking in accepted else None)
        for key, marking in by_key.items()
    }


@dataclass(frozen=True)
class ListedQuestion:
    question: Question
    method_label: str | None
    """The label of the method of the user's draft of its marking, or else of its marking, while that can mark."""
    is_marked: bool
    has_problems: bool
    """It has a marking that can't mark until fixed."""
    in_question_group: bool
    has_imported_marks: bool
    type: str
    proportion: float | None
    """The average proportion of the question's marks."""
    progress: float | None
    """How far a remark of it has got, while one runs."""
    has_draft: bool
    is_shown: bool

    @property
    def badge(self) -> MarkingBadge:
        if self.in_question_group and not self.has_draft:
            return MarkingBadge("Question group", "warning" if self.has_problems else "method")
        return marking_badge(
            self.method_label, has_problems=self.has_problems, has_imported_marks=self.has_imported_marks
        )


@dataclass(frozen=True)
class QuestionGroup:
    """A question group, or the user's draft of a new one."""

    url: str
    marked_keys: list[str]
    method_label: str
    has_draft: bool
    is_shown: bool


@dataclass(frozen=True)
class QuestionList:
    """The question list beside every marking page, highlighting what the page shows: `shown_id` is the ID of its
    question, question group or draft, or "new" for a new question group."""

    shown_id: str
    questions: list[ListedQuestion]
    question_groups: list[QuestionGroup]

    @property
    def marked_count(self) -> int:
        return sum(1 for question in self.questions if question.is_marked)

    @property
    def adding(self) -> bool:
        return self.shown_id == "new"


def question_list(
    membership: AssessmentAccess, shown_id: str, accepted: Mapping[Marking, Check] | None = None
) -> QuestionList:
    """`accepted`, the assessment's markings the core accepts, saves working them out again."""
    assessment = membership.assessment
    accepted = assessment_checks(assessment) if accepted is None else accepted
    markings = list(assessment.markings.select_related("question"))
    saved = saved_markings(markings, accepted)
    group_markings = [marking for marking in markings if marking.question_id is None]
    averages = average_proportions(assessment)
    progress = {key: job.progress for job in running_remarks(assessment) for key in job.question_keys}
    imported_keys = imported_mark_keys(assessment)
    drafts = list(
        MarkingDraftRecord.objects.filter(assessment=assessment, user=membership.member.user).select_related("question")
    )
    drafted_ids = {draft.question_id for draft in drafts} | {draft.marking_id for draft in drafts}
    draft_methods = _draft_methods(membership, drafts)
    listed = []
    for question in assessment.questions.all():
        saved_marking = saved.get(question.key)
        own_marking = saved_marking.marking if saved_marking and not saved_marking.in_question_group else None
        listed.append(
            ListedQuestion(
                question=question,
                method_label=draft_methods.get(question.pk) or (saved_marking.method_label if saved_marking else None),
                is_marked=saved_marking is not None and not saved_marking.has_problems,
                has_problems=saved_marking is not None and saved_marking.has_problems,
                in_question_group=saved_marking is not None and saved_marking.in_question_group,
                has_imported_marks=question.key in imported_keys,
                type=QUESTION_TYPE_LABELS.get(question.core_question.type, ""),
                proportion=averages.get(question.pk),
                progress=progress.get(question.key),
                has_draft=question.pk in drafted_ids or (own_marking is not None and own_marking.pk in drafted_ids),
                is_shown=str(question.pk) == shown_id,
            )
        )
    question_groups = [
        QuestionGroup(
            reverse("question_group", args=[assessment.pk, marking.pk]),
            sorted(questions_marked(marking, accepted.get(marking))),
            draft_methods.get(marking.pk) or (accepted[marking].method.label if marking in accepted else ""),
            marking.pk in drafted_ids,
            str(marking.pk) == shown_id,
        )
        for marking in group_markings
    ]
    for draft in drafts:
        if draft.question_id is None and draft.marking_id is None:
            new_group = MarkingWorkspace(draft)
            check = new_group.finalized
            url = reverse("question_group_draft", args=[assessment.pk, draft.pk])
            question_groups.append(
                QuestionGroup(
                    url,
                    sorted(check.questions_marked()) if check else [],
                    drafted_method(new_group.checks),
                    True,
                    str(draft.pk) == shown_id,
                )
            )
    return QuestionList(shown_id, listed, question_groups)


def _draft_methods(membership: AssessmentAccess, drafts: list[MarkingDraftRecord]) -> dict[UUID | None, str]:
    """The method of each of the user's `drafts`, by what it marks."""
    if not drafts:
        return {}
    questions = membership.assessment.question_set()
    methods = usable(membership.member.user, membership.course)
    return {
        draft.question_id or draft.marking_id: drafted_method(
            marking_draft(draft.definition, questions, draft.question.key if draft.question else None, methods)
        )
        for draft in drafts
    }


def drafted_method(checks: MarkingDraft) -> str:
    """The method of a draft, as far as it has one."""
    return checks.label("") if checks.is_check("") else ""


@dataclass(frozen=True)
class SavedGroup:
    """A saved question group, as its page and those of its questions show it."""

    marking: Marking
    marked_keys: list[str]
    """The questions it marks, sorted."""
    involved_keys: list[str]
    """The questions it marks or reads, in question order."""
    summary: MarkingSummary


def saved_group(assessment: Assessment, marking: Marking, check: Check | None) -> SavedGroup:
    """The question group `marking`, as `check`, the core's reading of it, while it can mark."""
    return SavedGroup(
        marking,
        sorted(questions_marked(marking, check)),
        list(assessment.questions.filter(key__in=questions_involved(marking, check)).values_list("key", flat=True)),
        _summary(marking, check, assessment),
    )


@dataclass(frozen=True)
class Panel:
    """The marking column of a question no one is editing: its saved marking, the question group marking it, or
    methods to choose from."""

    mode: Literal["summary", "question_group", "choose"]
    question_group: SavedGroup | None
    summary: MarkingSummary | None
    choices: Choices
    has_imported_marks: bool
    has_history: bool
    archived: bool
    """Whether the marking's method is archived."""


def panel(
    membership: AssessmentAccess,
    question: Question,
    marking: Marking | None,
    accepted: Mapping[Marking, Check] | None = None,
) -> Panel:
    """`accepted`, the assessment's markings the core accepts, saves working them out again."""
    assessment = membership.assessment
    accepted = assessment_checks(assessment) if accepted is None else accepted
    saved_group_marking = saved_markings(assessment.markings.filter(question=None), accepted).get(question.key)
    group_marking = saved_group_marking.marking if saved_group_marking else None
    group = (
        saved_group(assessment, group_marking, accepted.get(group_marking)) if group_marking and not marking else None
    )
    check = accepted.get(marking) if marking else accepted.get(group_marking) if group_marking else None
    choosing = marking is None and group_marking is None and membership.can_edit
    user, course = membership.member.user, assessment.course
    methods = methods_for(user, assessment, question.key) if choosing else Methods()
    return Panel(
        mode="summary" if marking else "question_group" if group else "choose",
        question_group=group,
        summary=_summary(marking, check, assessment) if marking else group.summary if group else None,
        choices=choices(methods, offers(user, course) if choosing else OfferedMethods((), (), {})),
        has_imported_marks=has_imported_marks(assessment, question.key),
        has_history=choosing and Marking.history.filter(question_id=question.pk).exists(),
        archived=check is not None and check.method.name in _archived_methods(marking or group_marking),
    )


def _archived_methods(marking: Marking | None) -> set[str]:
    """The archived methods `marking` uses."""
    methods = Method.objects.filter(versions__markings=marking, archived=True).select_related("library")
    return {method.full_name for method in methods}
