"""What an assessment's overview shows: its figures, what's left to do, a summary of the marks, what's worth a look in
the questions, and its activity."""

import statistics
from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby, takewhile
from typing import Any, Literal
from uuid import UUID

from django.conf import settings
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils.text import Truncator, get_text_list

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.dashboard.progress import Progress, progress
from gradeflow_app.imports.models import SubmissionsFile, imported_mark_keys
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.presenters import MarkingBadge, SavedMarking, marking_badge, saved_markings
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import (
    MIN_STUDENTS,
    QuestionSummary,
    Results,
    TotalStatistics,
    assessment_results,
    histogram,
)
from gradeflow_app.results.models import Adjustment
from gradeflow_app.ui.templatetags.ui import marks, percent

FIVE_NUMBER_SUMMARY = (
    ("Min", "minimum"),
    ("Q1", "lower_quartile"),
    ("Median", "median"),
    ("Q3", "upper_quartile"),
    ("Max", "maximum"),
)
MAX_CHANGES_READ = 500
"""The most changes to markings, and to adjustments, read for activity: enough for runs of them to fill a page."""
BAR_WIDTHS = (("coarse", "Coarse", -1), ("auto", "Auto", 0), ("fine", "Fine", 1))
"""The distribution's bar widths: name, label, and how many round widths finer than Rice's rule suggests."""
COMMON_WRONG_ANSWER_SHARE = 0.2
"""Of the students with marks for a question, how many give the same wrong answer for it to be worth a look."""
ALMOST_EVERYONE_SHARE = 0.95
"""Of the students with marks for a question, how many get full marks, or no marks, for it to be worth a look."""
MIN_BLANK_SHARE = 0.05
"""Of the students, how many leave a question blank for it to count among the blanks near the end."""
ACCEPTABLE_ALPHA = 0.7
ALPHA_RATINGS = (
    (0.9, "Excellent"),
    (0.8, "Good"),
    (ACCEPTABLE_ALPHA, "Acceptable"),
    (0.6, "Questionable"),
    (0.5, "Poor"),
)
"""George and Mallery's rule of thumb for Cronbach's alpha, by each rating's lowest value; below 0.5, Unacceptable."""


@dataclass(frozen=True)
class NextStep:
    """What's left to do, as the overview's banner says it, and the button that opens it."""

    message: str
    action: str
    url: str


@dataclass(frozen=True)
class Finding:
    """What stands out about `question`, or about the questions from it to `last_question`."""

    question: Question
    title: str
    detail: str
    icon: str
    warning: bool = False
    last_question: Question | None = None


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
    imported_keys = imported_mark_keys(assessment)
    assessment_progress = progress(assessment, accepted, imported_keys)
    results = assessment_results(assessment) if assessment_progress.latest_import else None
    results_with_marks = results if results and results.has_marks else None
    summaries = results.summaries() if results else []
    alpha = results_with_marks.cronbach_alpha if results_with_marks else None
    saved_by_key = saved_markings(assessment.markings.select_related("question"), accepted)
    return {
        "access": access,
        "progress": assessment_progress,
        "student_count": len(results.rows) if results else 0,
        "next_step": _next_step(assessment, assessment_progress, summaries) if results and access.can_edit else None,
        "results": results_with_marks,
        "questions": _questions(summaries, saved_by_key, imported_keys) if results_with_marks else [],
        "has_not_marked_answers": any(summary.not_marked_count for summary in summaries),
        "distribution": _distribution(results_with_marks) if results_with_marks else None,
        "box_plot": (
            _box_plot(results_with_marks.statistics, results_with_marks.max_marks) if results_with_marks else None
        ),
        "cronbach_alpha": alpha,
        "alpha_rating": _alpha_rating(alpha) if alpha is not None else "",
        "acceptable_alpha": ACCEPTABLE_ALPHA,
        "findings": _findings(summaries, len(results_with_marks.rows)) if results_with_marks else None,
    }


def _next_step(
    assessment: Assessment, assessment_progress: Progress, summaries: list[QuestionSummary]
) -> NextStep | None:
    """The questions and answers left to mark, opening the first of them; then sending the marks, until they're sent."""
    not_marked = [
        summary.question for summary in summaries if summary.question.key not in assessment_progress.marked_keys
    ]
    needing = [summary for summary in summaries if summary.unmarked_count]
    parts = []
    if not_marked:
        parts.append(f"{_questions_text(not_marked)} {pluralize(len(not_marked), "isn't,aren't")} marked")
    if needing:
        count = sum(summary.unmarked_count for summary in needing)
        questions = _questions_text([summary.question for summary in needing])
        parts.append(f"{count} answer{pluralize(count)} in {questions} need{pluralize(count, 's,')} marking")
    if parts:
        first = not_marked[0] if not_marked else needing[0].question
        url = reverse("question", args=[assessment.pk, first.pk])
        return NextStep(" and ".join(parts) + ".", f"Go to {first.key}", url)
    if assessment_progress.latest_sent_marks is None and settings.GRADEFLOW_CORS_PROXY_URL:
        return NextStep(
            "The marks aren't sent to Canvas yet.", "Send to Canvas", reverse("export", args=[assessment.pk])
        )
    return None


def _questions_text(questions: list[Question]) -> str:
    """Up to two questions by key, as in "Q3 and Q8"; more as how many."""
    if len(questions) > 2:
        return f"{len(questions)} questions"
    return " and ".join(question.key for question in questions)


def _questions(
    summaries: list[QuestionSummary], saved_by_key: Mapping[str, SavedMarking], imported_keys: Set[str]
) -> list[tuple[QuestionSummary, MarkingBadge]]:
    """Each question's summary beside its marking badge."""
    badges = []
    for summary in summaries:
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


def _alpha_rating(alpha: float) -> str:
    return next((rating for lowest, rating in ALPHA_RATINGS if alpha >= lowest), "Unacceptable")


def _findings(summaries: list[QuestionSummary], student_count: int) -> list[Finding] | None:
    """What stands out about the questions, most serious first; None for fewer than `MIN_STUDENTS` students."""
    if student_count < MIN_STUDENTS:
        return None
    with_marks = [summary for summary in summaries if summary.marked_count]
    findings = [
        Finding(
            summary.question,
            "Weaker students did better",
            _negative_detail(summary),
            "exclamation-triangle",
            warning=True,
        )
        for summary in with_marks
        if summary.has_negative_discrimination
    ]
    for summary in with_marks:
        if summary.has_negative_discrimination:
            continue
        marked_count, full_count = summary.marked_count, summary.full_marks_count
        full_or_partial = full_count + summary.partial_marks_count
        common_answer = _common_wrong_answer(summary)
        if full_count >= ALMOST_EVERYONE_SHARE * marked_count:
            everyone = "Everyone" if full_count == marked_count else "Almost everyone"
            findings.append(
                Finding(
                    summary.question, f"{everyone} got full marks", f"{full_count} of {marked_count}", "check-circle"
                )
            )
        elif full_or_partial <= (1 - ALMOST_EVERYONE_SHARE) * marked_count:
            no_one = "Almost no one" if full_or_partial else "No one"
            detail = common_answer or f"{full_or_partial} of {marked_count}"
            findings.append(Finding(summary.question, f"{no_one} got marks", detail, "x-circle"))
        elif common_answer:
            findings.append(Finding(summary.question, "Common wrong answer", common_answer, "users"))
    if trailing := _blanks_near_the_end(summaries, student_count):
        detail = f"{get_text_list([str(summary.blank_count) for summary in trailing], 'and')} left blank"
        findings.append(
            Finding(
                trailing[0].question,
                "More blanks near the end",
                detail,
                "arrow-trending-down",
                last_question=trailing[-1].question,
            )
        )
    return findings


def _common_wrong_answer(summary: QuestionSummary) -> str:
    """The most common wrong answer and how many gave it, when at least `COMMON_WRONG_ANSWER_SHARE` did."""
    if summary.most_common_wrong_answer is None:
        return ""
    answer, count = summary.most_common_wrong_answer
    return _answered(answer, count) if count >= COMMON_WRONG_ANSWER_SHARE * summary.marked_count else ""


def _negative_detail(summary: QuestionSummary) -> str:
    """The most common wrong answer, when more than one student gave it, else the thirds."""
    if summary.most_common_wrong_answer:
        answer, count = summary.most_common_wrong_answer
        if count > 1:
            return f"{_answered(answer, count)}, marked wrong"
    if thirds := summary.thirds:
        return f"Bottom third {percent(thirds.bottom)}, top third {percent(thirds.top)}"
    return ""


def _answered(answer: str, count: int) -> str:
    return f"{count} answered “{Truncator(answer).chars(40)}”"


def _blanks_near_the_end(summaries: list[QuestionSummary], student_count: int) -> list[QuestionSummary]:
    """The last questions, two or more, each left blank by at least `MIN_BLANK_SHARE` of the students and by twice as
    many as the questions before them on average."""
    min_blanks = max(MIN_BLANK_SHARE * student_count, 1)
    trailing_count = sum(1 for _ in takewhile(lambda summary: summary.blank_count >= min_blanks, reversed(summaries)))
    if trailing_count < 2 or trailing_count == len(summaries):
        return []
    earlier, trailing = summaries[:-trailing_count], summaries[-trailing_count:]
    earlier_average = statistics.fmean(summary.blank_count for summary in earlier)
    return trailing if all(summary.blank_count >= 2 * earlier_average for summary in trailing) else []


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
