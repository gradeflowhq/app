"""What the marking pages show, for their templates: the question list and the marking column."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from django.urls import reverse
from gradeflow_core import Check, GradeFlowError, Methods, QuestionSet

from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.editor.editing import located
from gradeflow_app.editor.guide import Choices, choices
from gradeflow_app.imports.models import file_mark_keys, has_file_marks
from gradeflow_app.libraries.collection import Offers, in_use, offers
from gradeflow_app.libraries.models import Method
from gradeflow_app.marking.checks import checks, draft_of, methods_for, questions_involved, questions_marked
from gradeflow_app.marking.models import Draft, Marking
from gradeflow_app.marking.remarks import active_jobs
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.questions.models import TYPE_LABELS, Question
from gradeflow_app.results.marks import average_proportions


@dataclass(frozen=True)
class Summary:
    """A marking as the core describes it, or, while it can't mark, what stops it."""

    method: str
    description: str
    problems: tuple[str, ...] = ()

    @property
    def broken(self) -> bool:
        return bool(self.problems)


def summary_of(check: Check) -> Summary:
    """A marking the core accepts."""
    try:
        return Summary(check.method.label, check.describe())
    except GradeFlowError as error:
        return Summary(check.method.label, "", (str(error),))


def saved_summary(definition: str, key: str | None, methods: Methods, questions: QuestionSet) -> Summary:
    """A saved marking of the question `key`, or of a question group, which can stop marking when the core or its
    questions change."""
    draft = draft_of(definition, questions, key, methods)
    if found := draft.problems:
        method = draft.method("")
        return Summary(method.label if method else "", "", tuple(dict.fromkeys(located(draft, each) for each in found)))
    return summary_of(draft.finalized())


def _summary(marking: Marking, check: Check | None, assessment: Assessment) -> Summary:
    """`marking` as `check`, the core's reading of it, while it can mark."""
    if check is not None:
        return summary_of(check)
    key = marking.question.key if marking.question else None
    return saved_summary(marking.definition, key, in_use(assessment.course), assessment.question_set())


@dataclass(frozen=True)
class Status:
    """How a question is marked, in a word or two: by a method, by its imported marks, or not until it's marked or
    fixed."""

    label: str
    tone: Literal["method", "imported", "warning"]


def status(method: str | None, *, broken: bool, from_file: bool) -> Status:
    """The status of a question marked with `method`, or else broken, or with only its imported marks."""
    if method:
        return Status(method, "method")
    if broken:
        return Status("Needs fixing", "warning")
    if from_file:
        return Status("Imported marks", "imported")
    return Status("Not marked", "warning")


@dataclass(frozen=True)
class Saved:
    """A question's saved marking: its own, or the question group's that marks it."""

    marking: Marking
    method: str | None
    """The label of its method, while it can mark."""

    @property
    def broken(self) -> bool:
        return self.method is None

    @property
    def grouped(self) -> bool:
        return self.marking.question_id is None


def saved_markings(markings: Iterable[Marking], accepted: Mapping[Marking, Check]) -> dict[str, Saved]:
    """The saved marking of each question that has one, by key."""
    # A question's own marking comes last, so it wins over a question group that still lists it.
    ordered = sorted(markings, key=lambda marking: marking.question_id is not None)
    by_key = {key: marking for marking in ordered for key in questions_marked(marking, accepted.get(marking))}
    return {
        key: Saved(marking, accepted[marking].method.label if marking in accepted else None)
        for key, marking in by_key.items()
    }


@dataclass(frozen=True)
class Listed:
    question: Question
    method: str | None
    """The label of the method of the user's draft of its marking, or else of its marking, while that can mark."""
    marked: bool
    broken: bool
    """It has a marking that can't mark until fixed."""
    grouped: bool
    """Marked by a question group."""
    from_file: bool
    type: str
    proportion: float | None
    """The average proportion of the question's marks."""
    progress: float | None
    """How far a remark of it has got, while one runs."""
    drafting: bool
    current: bool

    @property
    def status(self) -> Status:
        if self.grouped and not self.drafting:
            return Status("Question group", "warning" if self.broken else "method")
        return status(self.method, broken=self.broken, from_file=self.from_file)


@dataclass(frozen=True)
class QuestionGroup:
    """A question group, or the user's draft of a new one."""

    url: str
    marks: list[str]
    method: str
    drafting: bool
    current: bool


@dataclass(frozen=True)
class QuestionList:
    """The question list beside every marking page, highlighting what the page shows: `shown` is the ID of its
    question, question group or draft, or "new" for a new question group."""

    shown: str
    items: list[Listed]
    question_groups: list[QuestionGroup]

    @property
    def marked(self) -> int:
        return sum(1 for item in self.items if item.marked)

    @property
    def adding(self) -> bool:
        return self.shown == "new"


def question_list(membership: Membership, shown: str, accepted: Mapping[Marking, Check] | None = None) -> QuestionList:
    """`accepted`, the assessment's markings the core accepts, saves working them out again."""
    assessment = membership.assessment
    accepted = checks(assessment) if accepted is None else accepted
    markings = list(assessment.markings.select_related("question"))
    saved = saved_markings(markings, accepted)
    grouped = [marking for marking in markings if marking.question_id is None]
    averages = average_proportions(assessment)
    progress = {key: job.progress for job in active_jobs(assessment) for key in job.questions}
    from_file = file_mark_keys(assessment)
    drafts = list(Draft.objects.filter(assessment=assessment, user=membership.member.user))
    drafting = {draft.question_id for draft in drafts} | {draft.marking_id for draft in drafts}
    drafted = {draft.question_id or draft.marking_id: drafted_method(Workspace(draft)) for draft in drafts}
    items = []
    for question in assessment.questions.all():
        mine = saved.get(question.key)
        own = mine.marking if mine and not mine.grouped else None
        items.append(
            Listed(
                question=question,
                method=drafted.get(question.pk) or (mine.method if mine else None),
                marked=mine is not None and not mine.broken,
                broken=mine is not None and mine.broken,
                grouped=mine is not None and mine.grouped,
                from_file=question.key in from_file,
                type=TYPE_LABELS.get(question.core.type, ""),
                proportion=averages.get(question.pk),
                progress=progress.get(question.key),
                drafting=question.pk in drafting or (own is not None and own.pk in drafting),
                current=str(question.pk) == shown,
            )
        )
    question_groups = [
        QuestionGroup(
            reverse("question_group", args=[assessment.pk, marking.pk]),
            sorted(questions_marked(marking, accepted.get(marking))),
            drafted.get(marking.pk) or (accepted[marking].method.label if marking in accepted else ""),
            marking.pk in drafting,
            str(marking.pk) == shown,
        )
        for marking in grouped
    ]
    for draft in drafts:
        if draft.question_id is None and draft.marking_id is None:
            new = Workspace(draft)
            check = new.finished
            url = reverse("question_group_draft", args=[assessment.pk, draft.pk])
            question_groups.append(
                QuestionGroup(
                    url,
                    sorted(check.questions_marked()) if check else [],
                    drafted_method(new),
                    True,
                    str(draft.pk) == shown,
                )
            )
    return QuestionList(shown, items, question_groups)


def drafted_method(workspace: Workspace) -> str:
    """The method of a draft, as far as it has one."""
    return workspace.checks.label("") if workspace.checks.is_check("") else ""


@dataclass(frozen=True)
class SavedGroup:
    """A saved question group, as its page and those of its questions show it."""

    marking: Marking
    marks: list[str]
    """The questions it marks, sorted."""
    keys: list[str]
    """The questions it marks or reads, in question order."""
    summary: Summary


def saved_group(assessment: Assessment, marking: Marking, check: Check | None) -> SavedGroup:
    """The question group `marking`, as `check`, the core's reading of it, while it can mark."""
    involved = questions_involved(marking, check)
    return SavedGroup(
        marking,
        sorted(questions_marked(marking, check)),
        [key for key in assessment.questions.values_list("key", flat=True) if key in involved],
        _summary(marking, check, assessment),
    )


@dataclass(frozen=True)
class Panel:
    """The marking column of a question no one is editing: its saved marking, the question group marking it, or
    methods to choose from."""

    kind: Literal["summary", "question_group", "choose"]
    question_group: SavedGroup | None
    summary: Summary | None
    choices: Choices
    file_marks: bool
    has_history: bool
    archived: bool
    """Whether the marking's method is archived."""


def panel(
    membership: Membership, question: Question, marking: Marking | None, accepted: Mapping[Marking, Check] | None = None
) -> Panel:
    """`accepted`, the assessment's markings the core accepts, saves working them out again."""
    assessment = membership.assessment
    accepted = checks(assessment) if accepted is None else accepted
    grouped = saved_markings(assessment.markings.filter(question=None), accepted).get(question.key)
    in_group = grouped.marking if grouped else None
    group = saved_group(assessment, in_group, accepted.get(in_group)) if in_group and not marking else None
    check = accepted.get(marking) if marking else accepted.get(in_group) if in_group else None
    choosing = marking is None and in_group is None and membership.can_edit
    user, course = membership.member.user, assessment.course
    offered = methods_for(user, assessment, question.key) if choosing else Methods()
    return Panel(
        kind="summary" if marking else "question_group" if group else "choose",
        question_group=group,
        summary=_summary(marking, check, assessment) if marking else group.summary if group else None,
        choices=choices(offered, offers(user, course) if choosing else Offers((), (), {})),
        file_marks=has_file_marks(assessment, question.key),
        has_history=choosing and Marking.history.filter(question_id=question.pk).exists(),
        archived=check is not None and check.method.name in _archived(marking or in_group),
    )


def _archived(marking: Marking | None) -> set[str]:
    """The archived methods `marking` uses."""
    methods = Method.objects.filter(versions__markings=marking, archived=True).select_related("library")
    return {method.full_name for method in methods}
