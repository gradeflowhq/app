"""What an assessment's overview shows: setup progress, a summary of the marks, and recent activity."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import Any, Literal
from uuid import UUID

from django.template.defaultfilters import pluralize
from django.urls import reverse

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.dashboard.progress import progress
from gradeflow_app.imports.models import SubmissionsFile, imported_mark_keys
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.presenters import MarkingBadge, SavedMarking, marking_badge, saved_markings
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import QuestionSummary, Results, TotalStatistics, assessment_results, histogram
from gradeflow_app.results.models import Adjustment
from gradeflow_app.ui.templatetags.ui import marks

FIVE_NUMBER_SUMMARY = (
    ("Min", "minimum"),
    ("Q1", "lower_quartile"),
    ("Median", "median"),
    ("Q3", "upper_quartile"),
    ("Max", "maximum"),
)
OVERVIEW_ACTIVITY_LIMIT = 5
MAX_CHANGES_READ = 500
"""The most changes to markings, and to adjustments, read for activity: enough for runs of them to fill a page."""
BAR_WIDTHS = (("coarse", "Coarse", -1), ("auto", "Auto", 0), ("fine", "Fine", 1))
"""The distribution's bar widths: name, label, and how many round widths finer than Rice's rule suggests."""


@dataclass(frozen=True)
class Event:
    occurred_at: datetime
    user: User | None
    description: str
    url: str = ""
    """The page of the question or question group it changed, while that exists."""


@dataclass(frozen=True)
class _Change:
    """A change to the marking or adjustments of a question, or to a question group."""

    occurred_at: datetime
    user: User | None
    subject: UUID
    """The question or question group changed."""
    kind: Literal["marking", "adjustments"]
    subject_name: str
    """The subject in words: "marking for Q3", or for adjustments, "Q3"."""
    url: str
    action: str
    """What the change did: "added", "changed" or "removed" a marking; "adjusted" or "cleared" a student."""
    student: UUID | None = None


def overview_context(access: AssessmentAccess) -> dict[str, Any]:
    assessment = access.assessment
    accepted = assessment_checks(assessment)
    assessment_progress = progress(assessment, accepted)
    results = assessment_results(assessment) if assessment_progress.latest_import else None
    results_with_marks = results if results and results.has_marks else None
    latest_events = activity(assessment, limit=OVERVIEW_ACTIVITY_LIMIT + 1)
    saved_by_key = saved_markings(assessment.markings.select_related("question"), accepted)
    return {
        "access": access,
        "progress": assessment_progress,
        "results": results_with_marks,
        "questions": _questions(assessment, results_with_marks, saved_by_key) if results_with_marks else [],
        "has_adjustments": results_with_marks is not None and results_with_marks.has_adjustments,
        "distribution": _distribution(results_with_marks) if results_with_marks else None,
        "box_plot": (
            _box_plot(results_with_marks.statistics, results_with_marks.max_marks) if results_with_marks else None
        ),
        "activity": latest_events[:OVERVIEW_ACTIVITY_LIMIT],
        "has_more_activity": len(latest_events) > OVERVIEW_ACTIVITY_LIMIT,
    }


def _questions(
    assessment: Assessment, results: Results, saved_by_key: Mapping[str, SavedMarking]
) -> list[tuple[QuestionSummary, MarkingBadge]]:
    """Each question's summary beside how its saved marking marks it."""
    imported_keys = imported_mark_keys(assessment)
    badges = []
    for summary in results.summaries():
        key = summary.question.key
        saved_marking = saved_by_key.get(key)
        method_label = saved_marking.method_label if saved_marking else None
        badges.append(
            (
                summary,
                marking_badge(
                    method_label,
                    has_problems=saved_marking is not None and saved_marking.has_problems,
                    has_imported_marks=key in imported_keys,
                ),
            )
        )
    return badges


def _box_plot(statistics: TotalStatistics | None, max_marks: float) -> dict[str, Any] | None:
    """The statistics, with where each of the five numbers falls along the marks (as a percentage), for the box plot."""
    if statistics is None or not max_marks:
        return None
    positions = {name: round(100 * getattr(statistics, name) / max_marks, 1) for _, name in FIVE_NUMBER_SUMMARY}
    return {
        "statistics": statistics,
        "positions": positions,
        "box_width": round(positions["upper_quartile"] - positions["lower_quartile"], 1),
        "whisker_width": round(positions["maximum"] - positions["minimum"], 1),
        "five_numbers": [(label, marks(getattr(statistics, name))) for label, name in FIVE_NUMBER_SUMMARY],
    }


def _distribution(results: Results) -> dict[str, Any]:
    """The bands of totals at each bar width, and the widths to choose from: those that differ from Auto's."""
    bars = {name: histogram(results.totals, results.max_marks, finer_steps) for name, _, finer_steps in BAR_WIDTHS}
    widths = [(name, label, name != "auto" and bars[name] == bars["auto"]) for name, label, _ in BAR_WIDTHS]
    return {"chart": {"max_marks": results.max_marks, "bars": bars}, "widths": widths}


def activity(assessment: Assessment, limit: int) -> list[Event]:
    """The latest changes, newest first; a run of changes by one person to one question's marking, to its
    adjustments, or to one question group, counts once."""
    events = [
        Event(file.uploaded_at, file.uploaded_by, f"imported {file.name} ({_student_count_text(file.student_count)})")
        for file in SubmissionsFile.objects.filter(assessment=assessment).select_related("uploaded_by")[:limit]
    ]
    events += [
        Event(
            sent.sent_at,
            sent.sent_by,
            f"sent marks to “{sent.assignment_name}” ({_student_count_text(sent.student_count)})",
        )
        for sent in assessment.sent_marks.select_related("sent_by")[:limit]
    ]
    changes = sorted(_changes(assessment), key=lambda change: change.occurred_at, reverse=True)
    runs = groupby(changes, key=lambda change: (change.user, change.subject, change.kind))
    events += [_event(list(run)) for _, run in runs]
    events.sort(key=lambda event: event.occurred_at, reverse=True)
    return events[:limit]


def _changes(assessment: Assessment) -> list[_Change]:
    """The latest changes to the assessment's markings and adjustments."""
    questions = dict(assessment.questions.values_list("pk", "key"))
    groups = set(assessment.markings.filter(question=None).values_list("pk", flat=True))

    def question_url(question_id: UUID) -> str:
        return reverse("question", args=[assessment.pk, question_id]) if question_id in questions else ""

    versions = Marking.history.filter(assessment_id=assessment.pk).select_related("history_user")[:MAX_CHANGES_READ]
    keys = dict(
        Question.history.filter(id__in={version.question_id for version in versions})
        .order_by("history_date")
        .values_list("id", "key")
    )
    changes = []
    for version in versions:
        action = {"+": "added", "~": "changed", "-": "removed"}[version.history_type]
        if version.question_id:
            subject_name, url = f"marking for {keys[version.question_id]}", question_url(version.question_id)
        else:
            group_url = reverse("question_group", args=[assessment.pk, version.id]) if version.id in groups else ""
            subject_name, url = "a question group", group_url
        subject = version.question_id or version.id
        changes.append(
            _Change(version.history_date, version.history_user, subject, "marking", subject_name, url, action)
        )
    adjustments = (
        Adjustment.history.filter(question__assessment=assessment)
        .select_related("history_user")
        .order_by("-history_date")[:MAX_CHANGES_READ]
    )
    changes += [
        _Change(
            adjustment.history_date,
            adjustment.history_user,
            adjustment.question_id,
            "adjustments",
            questions.get(adjustment.question_id, "a question"),
            question_url(adjustment.question_id),
            "cleared" if adjustment.history_type == "-" else "adjusted",
            adjustment.student_id,
        )
        for adjustment in adjustments
    ]
    return changes


def _event(run: list[_Change]) -> Event:
    """A run of changes, newest first, as one event."""
    latest = run[0]
    actions = {change.action for change in run}
    if latest.kind == "marking":
        description = f"{latest.action if len(run) == 1 else 'changed'} {latest.subject_name}"
    elif len(actions) > 1:
        description = f"adjusted marks for {latest.subject_name}"
    else:
        students = _student_count_text(len({change.student for change in run}))
        action = (
            f"cleared adjustments to {latest.subject_name}"
            if actions == {"cleared"}
            else f"adjusted {latest.subject_name}"
        )
        description = f"{action} for {students}"
    return Event(latest.occurred_at, latest.user, description, latest.url)


def _student_count_text(count: int) -> str:
    return f"{count} student{pluralize(count)}"
