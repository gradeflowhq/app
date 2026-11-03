"""The answers column beside a marking, with the marks of the user's latest preview while they edit it."""

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from gradeflow_core import Check, QuestionResult, QuestionSet
from gradeflow_core.editing import DECISIONS, answer_decisions, can_mark_by_example
from gradeflow_core.editing import Marking as CoreMarking

from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.marking import previews
from gradeflow_app.marking.answers import answer_groups, comparisons
from gradeflow_app.marking.answers.answer_groups import AnswerGroup
from gradeflow_app.marking.answers.combinations import Combination, combinations
from gradeflow_app.marking.answers.out_of_date import asks_language_models, out_of_date_notices
from gradeflow_app.marking.checks import (
    assessment_checks,
    definition_data,
    questions_involved,
    questions_marked,
    runnable_check,
)
from gradeflow_app.marking.models import Marking, MarkingTarget
from gradeflow_app.marking.remarks import running_remarks
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.model_service.endpoints import asks_models
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request

GROUPINGS: dict[str, tuple[str, answer_groups.GroupingOptions]] = {
    "answers": ("Same answers", answer_groups.GroupingOptions()),
    "spelling": ("Similar spelling", answer_groups.GroupingOptions(similarity="spelling")),
    "meaning": ("Similar meaning", answer_groups.GroupingOptions(similarity="meaning")),
    "feedback": ("Same feedback", answer_groups.GroupingOptions(group_by="feedback")),
}
FILTERS = {
    "all": "All answers",
    "full": "Full marks",
    "partial": "Partial marks",
    "zero": "No marks",
    "blank": "Blank",
    "adjusted": "Adjusted",
    "unmarked": "Couldn't mark",
}
DECISION_BADGES = {"full_marks": "badge-success", "part_marks": "badge-warning", "not_allowed": "badge-error"}
"""How a decision marking by example gives answers is badged; one not here is plain."""
_VIEW_PARAMS = ("group", "threshold", "filter", "shown_count")
ANSWER_GROUPS_PER_PAGE = 100
"""How many answer groups the column shows at first, and how many more each "Show more" adds."""


@dataclass(frozen=True)
class PreviewSummary:
    """How many students a preview changes the marks of, and how many it couldn't mark (`problem` says why for the
    first). For a single question, also how many it gives full, partial and no marks."""

    changed_count: int
    unmarked_count: int
    problem: str
    full_marks_count: int = 0
    partial_marks_count: int = 0
    zero_marks_count: int = 0


def answers_view(request: Request, question: Question) -> dict[str, str]:
    """How this user views the question's answers: chosen in the query, else as they last left it."""
    key = f"answers:{question.pk}"
    remembered: dict[str, str] = request.session.get(key, {})
    submitted = {name: request.GET[name] for name in _VIEW_PARAMS if name in request.GET}
    if submitted.get("group", remembered.get("group")) != remembered.get("group"):
        # Another kind of grouping starts from its own default similarity.
        submitted.pop("threshold", None)
        remembered = {name: value for name, value in remembered.items() if name != "threshold"}
    chosen = remembered | submitted
    if "shown_count" not in submitted and any(chosen.get(name) != remembered.get(name) for name in ("group", "filter")):
        chosen.pop("shown_count", None)
    if chosen != request.session.get(key):
        request.session[key] = chosen
    return chosen


def grouping_of(view: dict[str, str]) -> str:
    return view["group"] if view.get("group") in GROUPINGS else "answers"


def shown_count(view: dict[str, str]) -> int:
    try:
        return max(int(view.get("shown_count", ANSWER_GROUPS_PER_PAGE)), ANSWER_GROUPS_PER_PAGE)
    except ValueError:
        return ANSWER_GROUPS_PER_PAGE


def grouping_options(view: dict[str, str]) -> answer_groups.GroupingOptions:
    chosen = GROUPINGS[grouping_of(view)][1]
    try:
        threshold = float(view.get("threshold", chosen.threshold))
    except ValueError:
        threshold = chosen.threshold
    if not math.isfinite(threshold):
        threshold = chosen.threshold
    return replace(chosen, threshold=min(max(threshold, 0.5), 1.0))


def draft_workspace(request: Request, membership: AssessmentAccess, subject: MarkingTarget) -> MarkingWorkspace | None:
    """The user's draft of the marking, if they're editing it."""
    draft = subject.user_draft(request.user) if membership.can_edit else None
    return MarkingWorkspace(draft) if draft else None


def answers_column_html(
    request: Request,
    membership: AssessmentAccess,
    subject: MarkingTarget,
    workspace: MarkingWorkspace | None,
    *,
    accepted: Mapping[Marking, Check] | None = None,
    out_of_band: bool = False,
) -> str:
    """The answers column beside a marking: students' answers to its question, or the combinations of answers it
    marks; with the marks of the user's latest preview of their draft in `workspace`. `accepted`, the assessment's
    markings the core accepts, saves working them out again."""
    if subject.question is None:
        context = combinations_context(request, membership, subject, workspace, accepted)
        return render_to_string("marking/combinations.html#answers", context | {"out_of_band": out_of_band}, request)
    context = answers_context(request, membership, subject.question, workspace, accepted)
    return render_to_string("results/answers.html#answers", context | {"out_of_band": out_of_band}, request)


def preview_bar(request: Request, workspace: MarkingWorkspace) -> str:
    """The bar offering to preview the draft, to swap in as it changes."""
    context = {"out_of_band": True} | draft_context(workspace)
    return render_to_string("results/answers.html#preview_bar", context, request)


def answers_context(
    request: Request,
    membership: AssessmentAccess,
    question: Question,
    workspace: MarkingWorkspace | None = None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """The answers column of a question; `workspace` is the user's draft of its marking, looked up if not given."""
    view = answers_view(request, question)
    options = grouping_options(view)
    student_answers = answer_groups.student_answers(question)
    comparison = None
    grouping_job = None
    needs_grouping = False
    failed_grouping_job = None
    texts = answer_groups.grouping_texts(student_answers, options)
    too_many_to_compare = options.similarity != "exact" and answer_groups.too_many_to_compare(texts)
    grouped_by = replace(options, similarity="normalized") if too_many_to_compare else options
    if texts and options.similarity == "meaning" and not too_many_to_compare:
        comparison = comparisons.cached_comparison(texts)
        if comparison is None:
            jobs = membership.assessment.jobs.filter(
                kind=Job.Kind.GROUPING,
                question_keys=[question.key],
                marked_with__comparison=comparisons.comparison_key(texts, core_version=CORE_VERSION),
            )
            grouping_job = jobs.running().first()
            last = None if grouping_job else jobs.first()
            failed_grouping_job = last if last is not None and last.status == Job.Status.FAILED else None
            needs_grouping = not grouping_job and not failed_grouping_job
    groups = answer_groups.group(student_answers, grouped_by, comparison)
    if workspace is None:
        workspace = draft_workspace(request, membership, MarkingTarget.of(membership.assessment, question))
    job, preview_results = previews.preview_and_results(workspace)
    preview_by_student = None
    if preview_results is not None:
        preview_by_student = {
            student_id: results[question.key]
            for student_id, results in preview_results.items()
            if question.key in results
        }
    if preview_by_student is not None:
        for group in groups:
            group.preview = _preview_marks(group, preview_by_student)
    filters = FILTERS | ({"changed": "Changed"} if preview_by_student else {})
    filter_name = view["filter"] if view.get("filter") in filters else "all"
    filtered = [group for group in groups if _passes_filter(group, filter_name)]
    count = shown_count(view)
    _set_group_decisions(question, workspace, filtered[:count])
    accepted = assessment_checks(membership.assessment) if accepted is None else accepted
    return {
        "membership": membership,
        "question": question,
        "out_of_date_notices": out_of_date_notices(accepted, [question.key]),
        "asks_models": _asks_models(accepted, [question.key], workspace),
        "answer_groups": filtered[:count],
        "show_more": count + ANSWER_GROUPS_PER_PAGE,
        "hidden_count": max(len(filtered) - count, 0),
        "student_count": len(student_answers),
        "grouping_options": options,
        "grouping": grouping_of(view),
        "groupings": {name: label for name, (label, _) in GROUPINGS.items()},
        "filter": filter_name,
        "filters": filters,
        "preview_job": job,
        "preview_summary": _preview_summary(groups) if preview_by_student is not None else None,
        "marking_job": _marking_job(membership.assessment, [question.key]),
        "grouping_job": grouping_job,
        "needs_grouping": needs_grouping,
        "failed_grouping_job": failed_grouping_job,
        "too_many_to_compare": too_many_to_compare,
        "can_mark_by_example": can_mark_by_example(question.core_question),
        "decisions": DECISIONS.items(),
        **draft_context(workspace),
    }


def group_by_meaning(request: Request, membership: AssessmentAccess, question: Question) -> None:
    """Prepare a reusable comparison for this question's current answers."""
    texts = answer_groups.grouping_texts(answer_groups.student_answers(question), GROUPINGS["meaning"][1])
    if texts and not answer_groups.too_many_to_compare(texts) and comparisons.cached_comparison(texts) is None:
        comparisons.start_grouping(membership.assessment, question, texts, request.user)


def combinations_context(
    request: Request,
    membership: AssessmentAccess,
    subject: MarkingTarget,
    workspace: MarkingWorkspace | None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """Students by their answers to the questions marked together, as the user's draft in `workspace` marks them if
    they're editing it, else as the saved marking does."""
    assessment = membership.assessment
    if subject.marking is not None:
        url = reverse("question_group_answers", args=[assessment.pk, subject.marking.pk])
    elif workspace is not None:
        url = reverse("question_group_draft_answers", args=[assessment.pk, workspace.draft.pk])
    else:
        raise Http404  # new marking of questions together exists only as its draft
    accepted = assessment_checks(assessment) if accepted is None else accepted
    if workspace is None and subject.marking is not None:
        saved = accepted.get(subject.marking)
        keys = sorted(questions_marked(subject.marking, saved))
        read_keys = sorted(questions_involved(subject.marking, saved) - set(keys))
    else:
        keys, read_keys = _marked_and_read_keys(workspace.finalized if workspace else None, assessment.question_set())
    job, results = previews.preview_and_results(workspace)
    rows = combinations(assessment, keys, results, read_keys)
    only_changed = request.GET.get("filter") == "changed" and results is not None
    shown_rows = [row for row in rows if row.changed] if only_changed else rows
    return {
        "membership": membership,
        "question_keys": keys,
        "out_of_date_notices": out_of_date_notices(
            accepted, set(keys) | set(subject.marking.questions_marked if subject.marking else [])
        ),
        "asks_models": _asks_models(accepted, keys, workspace),
        "rows": shown_rows,
        "student_count": sum(row.student_count for row in rows),
        "answers_url": url,
        "filter": "changed" if only_changed else "all",
        "preview_job": job,
        "preview_summary": _combinations_preview_summary(rows) if results is not None else None,
        "marking_job": _marking_job(assessment, keys),
        "is_question_group": True,
        **draft_context(workspace),
    }


def _asks_models(accepted: Mapping[Marking, Check], keys: Iterable[str], workspace: MarkingWorkspace | None) -> bool:
    """Whether the saved marking, or the draft being previewed, asks a language model."""
    finalized = workspace.finalized if workspace else None
    return asks_language_models(accepted, keys) or (finalized is not None and asks_models([finalized]))


def draft_context(workspace: MarkingWorkspace | None) -> dict[str, Any]:
    """The draft the answers column previews, whether the marks shown are out of date for it, and if so whether it is
    complete enough to preview again."""
    if workspace is None:
        return {"draft": None}
    needs_preview = previews.preview_is_stale(workspace)
    return {"draft": workspace.draft, "needs_preview": needs_preview, "can_preview": needs_preview and workspace.valid}


def _marking_job(assessment: Assessment, keys: Iterable[str]) -> Job | None:
    """The run marking any of `keys` with the saved markings, while one runs."""
    wanted = set(keys)
    return next((job for job in running_remarks(assessment) if wanted & set(job.question_keys)), None)


def _marked_and_read_keys(check: CoreMarking | None, questions: QuestionSet) -> tuple[list[str], list[str]]:
    """The questions a marking of questions together marks, and those it only reads, such as a condition's."""
    if (check := runnable_check(check, questions)) is None:
        return [], []
    marked = check.questions_marked()
    return sorted(marked), sorted(check.questions_read() - marked)


def _set_group_decisions(question: Question, workspace: MarkingWorkspace | None, groups: list[AnswerGroup]) -> None:
    if not can_mark_by_example(question.core_question):
        return
    saved = MarkingTarget.of(question.assessment, question).marking if workspace is None else None
    marking_data = workspace.checks.data if workspace else definition_data(saved.definition) if saved else {}
    methods = workspace.methods if workspace else course_methods(question.assessment.course)
    decisions = answer_decisions(marking_data, methods=methods, question=question.core_question)
    for group in groups:
        group_decisions = {decisions.get(entry.normalized) for entry in group.student_answers}
        group.decisions = [
            (label, DECISION_BADGES.get(decision, "badge-ghost"))
            for decision, label in DECISIONS.items()
            if decision in group_decisions
        ]
        group.has_mixed_decisions = bool(group.decisions) and (None in group_decisions or len(group.decisions) > 1)


def _preview_marks(group: AnswerGroup, preview_by_student: dict[str, QuestionResult]) -> answer_groups.Preview | None:
    entry_results = [
        (entry, preview_by_student[entry.encrypted_student_id])
        for entry in group.student_answers
        if entry.encrypted_student_id in preview_by_student
    ]
    results = [result for _, result in entry_results]
    if not results:
        return None
    preview_awards = [(entry, entry.with_preview(result)) for entry, result in entry_results]
    marks = sorted({award.marks for _, award in preview_awards if award.marks is not None})
    unmarked = [result for result in results if not result.is_marked]
    levels = Counter(award.level for _, award in preview_awards)
    return answer_groups.Preview(
        lowest_marks=marks[0],
        highest_marks=marks[-1],
        feedback=preview_awards[0][1].feedback,
        breakdown=answer_groups.commonest_breakdown(award for _, award in preview_awards),
        full_marks_count=levels["full"],
        partial_marks_count=levels["partial"],
        zero_marks_count=levels["zero"],
        changed_count=sum(entry.differs_from(award) for entry, award in preview_awards),
        unmarked_count=len(unmarked),
        all_unmarked=len(unmarked) == len(results),
        problem=unmarked[0].feedback if unmarked else "",
    )


def _preview_summary(groups: list[AnswerGroup]) -> PreviewSummary:
    group_previews = [group.preview for group in groups if group.preview]
    return PreviewSummary(
        changed_count=sum(preview.changed_count for preview in group_previews),
        unmarked_count=sum(preview.unmarked_count for preview in group_previews),
        problem=next((preview.problem for preview in group_previews if preview.problem), ""),
        full_marks_count=sum(preview.full_marks_count for preview in group_previews),
        partial_marks_count=sum(preview.partial_marks_count for preview in group_previews),
        zero_marks_count=sum(preview.zero_marks_count for preview in group_previews),
    )


def _combinations_preview_summary(rows: list[Combination]) -> PreviewSummary:
    unmarked = [row for row in rows if any(cell.problem for cell in row.cells)]
    return PreviewSummary(
        changed_count=sum(row.changed_count for row in rows),
        unmarked_count=sum(row.unmarked_count for row in unmarked),
        problem=next((cell.problem for row in unmarked for cell in row.cells if cell.problem), ""),
    )


def _passes_filter(group: AnswerGroup, filter_name: str) -> bool:
    levels = group.levels
    match filter_name:
        case "full" | "zero":
            return levels == {filter_name}
        case "partial":
            return bool(levels) and levels not in ({"full"}, {"zero"})
        case "blank":
            return group.is_blank
        case "adjusted":
            return group.adjusted_count > 0
        case "changed":
            return bool(group.preview and group.preview.changed)
        case "unmarked":
            return bool(group.preview.unmarked_count if group.preview else group.unmarked_count)
    return True
