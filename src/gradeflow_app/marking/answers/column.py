"""The answers column beside a marking, with the marks of the user's latest preview while they edit it."""

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from gradeflow_core import Check, QuestionResult
from gradeflow_core.editing import DECISIONS, can_mark_by_example
from gradeflow_core.editing import Marking as CoreMarking

from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.marking import previews
from gradeflow_app.marking.answers import answer_groups, comparisons
from gradeflow_app.marking.answers.answer_groups import AnswerGroup
from gradeflow_app.marking.answers.combinations import Combination, combinations
from gradeflow_app.marking.checks import assessment_checks, marking_draft, questions_involved, questions_marked
from gradeflow_app.marking.models import Marking, MarkingSubject
from gradeflow_app.marking.remarks import NO_MODEL_SERVICE, running_remarks
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request

GROUPINGS: dict[str, tuple[str, answer_groups.GroupingOptions]] = {
    "answers": ("Same answers", answer_groups.GroupingOptions()),
    "spelling": ("Similar spelling", answer_groups.GroupingOptions(similarity="spelling")),
    "meaning": ("Similar meaning", answer_groups.GroupingOptions(similarity="meaning")),
    "feedback": ("Same feedback", answer_groups.GroupingOptions(group_by="feedback")),
}
FILTERS: dict[str, tuple[str, Callable[[AnswerGroup], bool]]] = {
    "all": ("All answers", lambda group: True),
    "full": ("Full marks", lambda group: group.levels == {"full"}),
    "partial": ("Partial marks", lambda group: bool(group.levels) and group.levels not in ({"full"}, {"no_marks"})),
    "no_marks": ("No marks", lambda group: group.levels == {"no_marks"}),
    "blank": ("Blank", lambda group: group.is_blank),
    "adjusted": ("Adjusted", lambda group: group.adjusted_count > 0),
    "unmarked": (
        "Couldn't mark",
        lambda group: bool(group.preview.unmarked_count if group.preview else group.unmarked_count),
    ),
    "changed": ("Changed", lambda group: bool(group.preview and group.preview.changed)),
}
"""Each filter of answer groups: its label, and which groups it shows. "Changed" is offered only with a preview."""
DECISION_BADGES = {"full_marks": "badge-success", "partial_marks": "badge-warning", "not_allowed": "badge-error"}
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
    no_marks_count: int = 0


def answers_view(request: Request, question: Question) -> dict[str, str]:
    """How this user views the question's answers: chosen in the query, else as they last left it. Remembers it in
    the session."""
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


def draft_workspace(request: Request, access: AssessmentAccess, subject: MarkingSubject) -> MarkingWorkspace | None:
    """The user's draft of the marking, if they're editing it."""
    draft = subject.user_draft(request.user) if access.can_edit else None
    return MarkingWorkspace(draft) if draft else None


def answers_column_html(
    request: Request,
    access: AssessmentAccess,
    subject: MarkingSubject,
    workspace: MarkingWorkspace | None,
    *,
    accepted: Mapping[Marking, Check] | None = None,
    out_of_band: bool = False,
) -> str:
    """The answers column beside a marking: students' answers to its question, or the combinations of answers it
    marks; with the marks of the user's latest preview of their draft in `workspace`. `accepted`, the assessment's
    markings the core accepts, saves working them out again."""
    if subject.question is None:
        context = combinations_context(request, access, subject, workspace, accepted)
        return render_to_string("marking/combinations.html#answers", context | {"out_of_band": out_of_band}, request)
    context = answers_context(request, access, subject, workspace, accepted)
    return render_to_string("results/answers.html#answers", context | {"out_of_band": out_of_band}, request)


def preview_bar(request: Request, workspace: MarkingWorkspace) -> str:
    """The bar offering to preview the draft, to swap in as it changes."""
    context = {"out_of_band": True} | draft_context(workspace, previews.preview_state(workspace.draft))
    return render_to_string("results/answers.html#preview_bar", context, request)


def answers_context(
    request: Request,
    access: AssessmentAccess,
    subject: MarkingSubject,
    workspace: MarkingWorkspace | None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """The answers column of a question's marking, with the marks of the user's latest preview of their draft in
    `workspace`."""
    question = subject.question
    assert question is not None
    view = answers_view(request, question)
    options = grouping_options(view)
    student_answers = answer_groups.student_answers(question)
    texts = answer_groups.grouping_texts(student_answers, options)
    too_many_to_compare = options.similarity != "exact" and answer_groups.too_many_to_compare(texts)
    grouped_by = replace(options, similarity="normalized") if too_many_to_compare else options
    compares_meaning = bool(texts) and options.similarity == "meaning" and not too_many_to_compare
    comparison = comparisons.cached_comparison(texts) if compares_meaning else None
    needs_comparison = compares_meaning and comparison is None
    grouping = comparisons.grouping_state(question, texts) if needs_comparison else None
    grouping_job = grouping if grouping is not None and grouping.is_active else None
    failed_grouping_job = grouping if grouping is not None and grouping.status == "failed" else None
    groups = answer_groups.group(student_answers, grouped_by, comparison)
    job, preview_results = previews.preview_and_results(workspace)
    preview_by_student = None
    if preview_results is not None:
        preview_by_student = {
            student_id: results[question.key]
            for student_id, results in preview_results.items()
            if question.key in results
        }
        for group in groups:
            group.preview = _preview_marks(group, preview_by_student)
    filters = {name: label for name, (label, _) in FILTERS.items() if name != "changed" or preview_by_student}
    filter_name = view["filter"] if view.get("filter") in filters else "all"
    shows = FILTERS[filter_name][1]
    filtered = [group for group in groups if shows(group)]
    count = shown_count(view)
    _set_group_decisions(subject, workspace, filtered[:count])
    accepted = assessment_checks(access.assessment) if accepted is None else accepted
    return {
        "access": access,
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
        "marking_job": _marking_job(access.assessment, [question.key]),
        "grouping_job": grouping_job,
        "needs_grouping": needs_comparison and not grouping_job and not failed_grouping_job,
        "failed_grouping_job": failed_grouping_job,
        "too_many_to_compare": too_many_to_compare,
        "can_mark_by_example": can_mark_by_example(question.core_question),
        "decisions": DECISIONS.items(),
        **draft_context(workspace, job),
    }


def group_by_meaning(request: Request, access: AssessmentAccess, question: Question) -> None:
    """Prepare a reusable comparison for this question's current answers."""
    texts = answer_groups.grouping_texts(answer_groups.student_answers(question), GROUPINGS["meaning"][1])
    if texts and not answer_groups.too_many_to_compare(texts) and comparisons.cached_comparison(texts) is None:
        comparisons.start_grouping(question, texts)


def combinations_context(
    request: Request,
    access: AssessmentAccess,
    subject: MarkingSubject,
    workspace: MarkingWorkspace | None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """Students by their answers to the questions marked together, as the user's draft in `workspace` marks them if
    they're editing it, else as the saved marking does."""
    assessment = access.assessment
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
        keys, read_keys = _marked_and_read_keys(workspace.finalized if workspace else None)
    job, results = previews.preview_and_results(workspace)
    rows = combinations(assessment, keys, results, read_keys)
    only_changed = request.GET.get("filter") == "changed" and results is not None
    shown_rows = [row for row in rows if row.changed] if only_changed else rows
    return {
        "access": access,
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
        **draft_context(workspace, job),
    }


def out_of_date_notices(accepted: Mapping[Marking, Check], question_keys: Iterable[str]) -> list[str]:
    """Why the marks of these questions are from before: their marking, one of the `accepted` ones, failed or wasn't
    run. One that can't mark says why in its own column; one marking them now shows its progress."""
    keys = set(question_keys)
    messages = []
    for marking in accepted:
        if not keys & marking.questions_given_marks:
            continue
        if marking.failure == NO_MODEL_SERVICE:
            messages.append(
                "Not marked again: the change came from someone without an AI service. The marks shown are from before."
            )
        elif marking.failure:
            messages.append(f"Marking failed: {marking.failure} The marks shown are from before.")
    return messages


def _asks_models(accepted: Mapping[Marking, Check], keys: Iterable[str], workspace: MarkingWorkspace | None) -> bool:
    """Whether a saved marking of these questions, one of the `accepted` ones, or the draft being previewed, asks a
    language model, which needs a model service."""
    wanted = set(keys)
    checks = [check for marking, check in accepted.items() if wanted & marking.questions_given_marks]
    if workspace is not None and (finalized := workspace.finalized) is not None:
        checks.append(finalized)
    return any(check.needs_model_endpoint() for check in checks)


def draft_context(workspace: MarkingWorkspace | None, preview: RunState | None) -> dict[str, Any]:
    """The draft the answers column previews, whether the marks shown are out of date for it, and if so whether it is
    complete enough to preview again. `preview` is how its latest preview went; one the runner lost counts as
    none."""
    if workspace is None:
        return {"draft": None}
    needs_preview = previews.preview_is_stale(workspace) or (
        workspace.draft.preview_input is not None and preview is None
    )
    return {"draft": workspace.draft, "needs_preview": needs_preview, "can_preview": needs_preview and workspace.valid}


def _marking_job(assessment: Assessment, keys: Iterable[str]) -> RunState | None:
    """The run marking any of `keys` with the saved markings, while one runs."""
    wanted = set(keys)
    return next(
        (state for marking, state in running_remarks(assessment) if wanted & set(marking.questions_marked)), None
    )


def _marked_and_read_keys(check: CoreMarking | None) -> tuple[list[str], list[str]]:
    """The questions a marking of questions together marks, and those it only reads, such as a condition's."""
    if check is None:
        return [], []
    marked = check.questions_marked()
    return sorted(marked), sorted(check.questions_read() - marked)


def _set_group_decisions(
    subject: MarkingSubject, workspace: MarkingWorkspace | None, groups: list[AnswerGroup]
) -> None:
    """Badge each group with the decisions the draft in `workspace`, else the saved marking, gives its answers."""
    assert subject.question is not None
    if not can_mark_by_example(subject.question.core_question):
        return
    if workspace is None:
        assessment = subject.assessment
        draft = marking_draft(
            subject.marking.definition if subject.marking else "",
            assessment.question_set(),
            subject.key,
            course_methods(assessment.course),
        )
    else:
        draft = workspace.checks
    decisions = draft.answer_decisions()
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
    preview_awards = [(entry, replace(entry, preview_result=result)) for entry, result in entry_results]
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
        no_marks_count=levels["no_marks"],
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
        no_marks_count=sum(preview.no_marks_count for preview in group_previews),
    )


def _combinations_preview_summary(rows: list[Combination]) -> PreviewSummary:
    unmarked = [row for row in rows if any(cell.problem for cell in row.cells)]
    return PreviewSummary(
        changed_count=sum(row.changed_count for row in rows),
        unmarked_count=sum(row.unmarked_count for row in unmarked),
        problem=next((cell.problem for row in unmarked for cell in row.cells if cell.problem), ""),
    )
