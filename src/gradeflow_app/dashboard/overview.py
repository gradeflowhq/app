"""What an assessment's overview shows: setup progress, a summary of the marks, and recent activity."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import Any, Literal

from django.template.defaultfilters import pluralize
from django.urls import reverse

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.dashboard.progress import progress
from gradeflow_app.imports.models import ResultsFile, file_mark_keys
from gradeflow_app.marking.checks import checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.presenters import Saved, Status, saved_markings, status
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import QuestionSummary, Results, Stats, histogram, results
from gradeflow_app.results.models import Adjustment
from gradeflow_app.ui.templatetags.ui import marks

FIVE_NUMBERS = (("Min", "minimum"), ("Q1", "q1"), ("Median", "median"), ("Q3", "q3"), ("Max", "maximum"))
ACTIVITY_SHOWN = 5
CHANGES_READ = 500
"""The most changes to markings, and to adjustments, read for activity: enough for runs of them to fill a page."""
WIDTHS = (("coarse", "Coarse", -1), ("auto", "Auto", 0), ("fine", "Fine", 1))
"""The distribution's bar widths: name, label, and how many round widths finer than Rice's rule suggests."""


@dataclass(frozen=True)
class Event:
    at: datetime
    who: User | None
    what: str
    url: str = ""
    """The page of the question or question group it changed, while that exists."""


@dataclass(frozen=True)
class _Change:
    """A change to the marking or adjustments of a question, or to a question group."""

    at: datetime
    who: User | None
    subject: object
    """The question or question group changed."""
    kind: Literal["marking", "adjustments"]
    name: str
    """The subject in words: "marking for Q3", or for adjustments, "Q3"."""
    url: str
    verb: str
    """What the change did: "set up", "changed" or "removed" a marking; "adjusted" or "cleared" a student."""
    student: object = None


def context(membership: Membership) -> dict[str, Any]:
    assessment = membership.assessment
    accepted = checks(assessment)
    done = progress(assessment, accepted)
    assessment_results = results(assessment) if done.imported else None
    with_marks = assessment_results if assessment_results and assessment_results.any_marks else None
    activity_shown = activity(assessment, limit=ACTIVITY_SHOWN + 1)
    saved = saved_markings(assessment.markings.select_related("question"), accepted)
    return {
        "membership": membership,
        "progress": done,
        "results": with_marks,
        "questions": _questions(assessment, with_marks, saved) if with_marks else [],
        "adjusted": with_marks is not None and with_marks.any_adjusted,
        "distribution": _distribution(with_marks) if with_marks else None,
        "spread": _spread(with_marks.stats, with_marks.max_marks) if with_marks else None,
        "activity": activity_shown[:ACTIVITY_SHOWN],
        "more_activity": len(activity_shown) > ACTIVITY_SHOWN,
    }


def _questions(
    assessment: Assessment, marked: Results, saved: Mapping[str, Saved]
) -> list[tuple[QuestionSummary, Status]]:
    """Each question's summary beside how its saved marking marks it."""
    from_file = file_mark_keys(assessment)
    statuses = []
    for summary in marked.summaries():
        key = summary.question.key
        mine = saved.get(key)
        method = mine.method if mine else None
        statuses.append((summary, status(method, broken=mine is not None and mine.broken, from_file=key in from_file)))
    return statuses


def _spread(stats: Stats | None, max_marks: float) -> dict[str, Any] | None:
    """The stats, with where each of the five numbers falls along the marks (as a percentage), for the box plot."""
    if stats is None or not max_marks:
        return None
    at = {name: round(100 * getattr(stats, name) / max_marks, 1) for _, name in FIVE_NUMBERS}
    return {
        "stats": stats,
        "at": at,
        "box": round(at["q3"] - at["q1"], 1),
        "whisker": round(at["maximum"] - at["minimum"], 1),
        "five": [(label, marks(getattr(stats, name))) for label, name in FIVE_NUMBERS],
    }


def _distribution(marked: Results) -> dict[str, Any]:
    """The bands of totals at each bar width, and the widths to choose from: those that differ from Auto's."""
    bars = {name: histogram(marked.totals, marked.max_marks, finer) for name, _, finer in WIDTHS}
    widths = [(name, label, name != "auto" and bars[name] == bars["auto"]) for name, label, _ in WIDTHS]
    return {"chart": {"max_marks": marked.max_marks, "bars": bars}, "widths": widths}


def activity(assessment: Assessment, limit: int) -> list[Event]:
    """The latest changes, newest first; a run of changes by one person to one question's marking, to its
    adjustments, or to one question group, counts once."""
    events = [
        Event(file.uploaded_at, file.uploaded_by, f"imported {file.name} ({_students(file.student_count)})")
        for file in ResultsFile.objects.filter(assessment=assessment).select_related("uploaded_by")[:limit]
    ]
    events += [
        Event(sent.sent_at, sent.sent_by, f"sent marks to “{sent.assignment_name}” ({_students(sent.student_count)})")
        for sent in assessment.publications.select_related("sent_by")[:limit]
    ]
    changes = sorted(_changes(assessment), key=lambda change: change.at, reverse=True)
    runs = groupby(changes, key=lambda change: (change.who, change.subject, change.kind))
    events += [_event(list(run)) for _, run in runs]
    events.sort(key=lambda event: event.at, reverse=True)
    return events[:limit]


def _changes(assessment: Assessment) -> list[_Change]:
    """The latest changes to the assessment's markings and adjustments."""
    questions = dict(assessment.questions.values_list("pk", "key"))
    groups = set(assessment.markings.filter(question=None).values_list("pk", flat=True))

    def question_url(question_id: object) -> str:
        return reverse("question", args=[assessment.pk, question_id]) if question_id in questions else ""

    versions = Marking.history.filter(assessment_id=assessment.pk).select_related("history_user")[:CHANGES_READ]
    keys = dict(
        Question.history.filter(id__in={version.question_id for version in versions})
        .order_by("history_date")
        .values_list("id", "key")
    )
    changes = []
    for version in versions:
        verb = {"+": "set up", "~": "changed", "-": "removed"}[version.history_type]
        if version.question_id:
            name, url = f"marking for {keys[version.question_id]}", question_url(version.question_id)
        else:
            group_url = reverse("question_group", args=[assessment.pk, version.id]) if version.id in groups else ""
            name, url = "a question group", group_url
        subject = version.question_id or version.id
        changes.append(_Change(version.history_date, version.history_user, subject, "marking", name, url, verb))
    adjustments = (
        Adjustment.history.filter(question__assessment=assessment)
        .select_related("history_user")
        .order_by("-history_date")[:CHANGES_READ]
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
    verbs = {change.verb for change in run}
    if latest.kind == "marking":
        what = f"{latest.verb if len(run) == 1 else 'changed'} {latest.name}"
    elif len(verbs) > 1:
        what = f"adjusted marks for {latest.name}"
    else:
        students = _students(len({change.student for change in run}))
        did = f"cleared adjustments to {latest.name}" if verbs == {"cleared"} else f"adjusted {latest.name}"
        what = f"{did} for {students}"
    return Event(latest.at, latest.who, what, latest.url)


def _students(count: int) -> str:
    return f"{count} student{pluralize(count)}"
