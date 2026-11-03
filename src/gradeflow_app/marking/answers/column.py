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

from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.core_build import CORE_BUILD
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.collection import in_use
from gradeflow_app.marking import previews
from gradeflow_app.marking.answers import answer_groups, comparisons
from gradeflow_app.marking.answers.answer_groups import AnswerGroup
from gradeflow_app.marking.answers.combinations import Combination, combinations
from gradeflow_app.marking.answers.status import marked_by_models, notices
from gradeflow_app.marking.checks import checks, questions_involved, questions_marked, runnable, tree_of
from gradeflow_app.marking.models import Marking, Subject
from gradeflow_app.marking.remarks import active_jobs
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.model_service.endpoints import asks_models
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request

GROUPINGS: dict[str, tuple[str, answer_groups.Options]] = {
    "answers": ("Same answers", answer_groups.Options()),
    "spelling": ("Similar spelling", answer_groups.Options(similar="spelling")),
    "meaning": ("Similar meaning", answer_groups.Options(similar="meaning")),
    "feedback": ("Same feedback", answer_groups.Options(by="feedback")),
}
FILTERS = {
    "all": "All answers",
    "full": "Full marks",
    "part": "Partial marks",
    "zero": "No marks",
    "blank": "Blank",
    "adjusted": "Adjusted",
    "unmarked": "Couldn't mark",
}
BADGES = {"full": "badge-success", "part": "badge-warning", "not_allowed": "badge-error"}
"""How a decision marking by example gives answers is badged; one not here is plain."""
_VIEW = ("group", "threshold", "filter", "shown")
SHOWN = 100
"""How many answer groups the column shows at first, and how many more each "Show more" adds."""


@dataclass(frozen=True)
class PreviewSummary:
    """How many students a preview changes the marks of, and how many it couldn't mark (`problem` says why for the
    first). For a single question, also how many it gives full, part and no marks."""

    changed: int
    unmarked: int
    problem: str
    full: int = 0
    part: int = 0
    zero: int = 0


def view_of(request: Request, question: Question) -> dict[str, str]:
    """How this user views the question's answers: chosen in the query, else as they last left it."""
    key = f"answers:{question.pk}"
    remembered: dict[str, str] = request.session.get(key, {})
    submitted = {name: request.GET[name] for name in _VIEW if name in request.GET}
    if submitted.get("group", remembered.get("group")) != remembered.get("group"):
        # Another kind of grouping starts from its own default similarity.
        submitted.pop("threshold", None)
        remembered = {name: value for name, value in remembered.items() if name != "threshold"}
    chosen = remembered | submitted
    if "shown" not in submitted and any(chosen.get(name) != remembered.get(name) for name in ("group", "filter")):
        chosen.pop("shown", None)
    if chosen != request.session.get(key):
        request.session[key] = chosen
    return chosen


def grouping_of(view: dict[str, str]) -> str:
    return view["group"] if view.get("group") in GROUPINGS else "answers"


def shown_of(view: dict[str, str]) -> int:
    try:
        return max(int(view.get("shown", SHOWN)), SHOWN)
    except ValueError:
        return SHOWN


def options(view: dict[str, str]) -> answer_groups.Options:
    chosen = GROUPINGS[grouping_of(view)][1]
    try:
        threshold = float(view.get("threshold", chosen.threshold))
    except ValueError:
        threshold = chosen.threshold
    if not math.isfinite(threshold):
        threshold = chosen.threshold
    return replace(chosen, threshold=min(max(threshold, 0.5), 1.0))


def editing(request: Request, membership: Membership, subject: Subject) -> Workspace | None:
    """The user's draft of the marking, if they're editing it."""
    draft = subject.draft_of(request.user) if membership.can_edit else None
    return Workspace(draft) if draft else None


def column(
    request: Request,
    membership: Membership,
    subject: Subject,
    workspace: Workspace | None,
    *,
    accepted: Mapping[Marking, Check] | None = None,
    oob: bool = False,
) -> str:
    """The answers column beside a marking: students' answers to its question, or the combinations of answers it
    marks; with the marks of the user's latest preview of their draft in `workspace`. `accepted`, the assessment's
    markings the core accepts, saves working them out again."""
    if subject.question is None:
        context = combinations_context(request, membership, subject, workspace, accepted)
        return render_to_string("marking/combinations.html#answers", context | {"oob": oob}, request)
    context = answers_context(request, membership, subject.question, workspace, accepted)
    return render_to_string("results/answers.html#answers", context | {"oob": oob}, request)


def preview_bar(request: Request, workspace: Workspace) -> str:
    """The bar offering to preview the draft, to swap in as it changes."""
    context = {"oob": True} | drafting(workspace)
    return render_to_string("results/answers.html#preview_bar", context, request)


def answers_context(
    request: Request,
    membership: Membership,
    question: Question,
    workspace: Workspace | None = None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """The answers column of a question; `workspace` is the user's draft of its marking, looked up if not given."""
    view = view_of(request, question)
    chosen = options(view)
    entries = answer_groups.entries(question)
    comparison = None
    waiting = None
    grouping_due = False
    grouping_failed = None
    keys = answer_groups.texts_of(entries, chosen)
    too_many = chosen.similar != "exact" and answer_groups.too_many(keys)
    grouped_by = replace(chosen, similar="normalized") if too_many else chosen
    if keys and chosen.similar == "meaning" and not too_many:
        comparison = comparisons.cached_comparison(keys)
        if comparison is None:
            jobs = membership.assessment.jobs.filter(
                kind=Job.Kind.GROUPING,
                questions=[question.key],
                provenance__comparison=comparisons.comparison_key(keys, core_build=CORE_BUILD),
            )
            waiting = jobs.running().first()
            last = None if waiting else jobs.first()
            grouping_failed = last if last is not None and last.status == Job.Status.FAILED else None
            grouping_due = not waiting and not grouping_failed
    groups = answer_groups.group(entries, grouped_by, comparison)
    if workspace is None:
        workspace = editing(request, membership, Subject.of(membership.assessment, question))
    job, marked = previews.previewed(workspace)
    preview = None
    if marked is not None:
        preview = {answer: marks[question.key] for answer, marks in marked.items() if question.key in marks}
    if preview is not None:
        for group in groups:
            group.preview = _preview_marks(group, preview)
    filters = FILTERS | ({"changed": "Changed"} if preview else {})
    filter_name = view["filter"] if view.get("filter") in filters else "all"
    filtered = [group for group in groups if _matches(group, filter_name)]
    shown = shown_of(view)
    _group_actions(question, workspace, filtered[:shown])
    accepted = checks(membership.assessment) if accepted is None else accepted
    return {
        "membership": membership,
        "question": question,
        "evaluation_notices": notices(accepted, [question.key]),
        "asks_models": _asks_models(accepted, [question.key], workspace),
        "answer_groups": filtered[:shown],
        "show_more": shown + SHOWN,
        "hidden": max(len(filtered) - shown, 0),
        "students": len(entries),
        "options": chosen,
        "grouping": grouping_of(view),
        "groupings": {name: label for name, (label, _) in GROUPINGS.items()},
        "filter": filter_name,
        "filters": filters,
        "preview_job": job,
        "preview_summary": _preview_summary(groups) if preview is not None else None,
        "marking_job": _marking_job(membership.assessment, [question.key]),
        "waiting": waiting,
        "grouping_due": grouping_due,
        "grouping_failed": grouping_failed,
        "too_many": too_many,
        "by_example": can_mark_by_example(question.core),
        "decisions": DECISIONS.items(),
        **drafting(workspace),
    }


def group_by_meaning(request: Request, membership: Membership, question: Question) -> None:
    """Prepare a reusable comparison for this question's current answers."""
    keys = answer_groups.texts_of(answer_groups.entries(question), GROUPINGS["meaning"][1])
    if keys and not answer_groups.too_many(keys) and comparisons.cached_comparison(keys) is None:
        comparisons.start_grouping(membership.assessment, question, keys, request.user)


def combinations_context(
    request: Request,
    membership: Membership,
    subject: Subject,
    workspace: Workspace | None,
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
    accepted = checks(assessment) if accepted is None else accepted
    if workspace is None and subject.marking is not None:
        saved = accepted.get(subject.marking)
        keys = sorted(questions_marked(subject.marking, saved))
        read = sorted(questions_involved(subject.marking, saved) - set(keys))
    else:
        keys, read = _questions(workspace.finished if workspace else None, assessment.question_set())
    job, results = previews.previewed(workspace)
    rows = combinations(assessment, keys, results, read)
    changed = request.GET.get("filter") == "changed" and results is not None
    listed = [row for row in rows if row.changed] if changed else rows
    return {
        "membership": membership,
        "keys": keys,
        "evaluation_notices": notices(
            accepted, set(keys) | set(subject.marking.output_keys if subject.marking else [])
        ),
        "asks_models": _asks_models(accepted, keys, workspace),
        "rows": listed,
        "students": sum(row.count for row in rows),
        "answers_url": url,
        "filter": "changed" if changed else "all",
        "preview_job": job,
        "preview_summary": _across_summary(rows) if results is not None else None,
        "marking_job": _marking_job(assessment, keys),
        "across": True,
        **drafting(workspace),
    }


def _asks_models(accepted: Mapping[Marking, Check], keys: Iterable[str], workspace: Workspace | None) -> bool:
    """Whether the saved marking, or the draft being previewed, asks a language model."""
    drafted = workspace.finished if workspace else None
    return marked_by_models(accepted, keys) or (drafted is not None and asks_models([drafted]))


def drafting(workspace: Workspace | None) -> dict[str, Any]:
    """The draft the answers column previews, whether the marks shown are out of date for it, and if so whether it is
    complete enough to preview again."""
    if workspace is None:
        return {"draft": None}
    due = previews.stale(workspace)
    return {"draft": workspace.draft, "preview_due": due, "preview_ready": due and workspace.valid}


def _marking_job(assessment: Assessment, keys: Iterable[str]) -> Job | None:
    """The run marking any of `keys` with the saved markings, while one runs."""
    shown = set(keys)
    return next((job for job in active_jobs(assessment) if shown & set(job.questions)), None)


def _questions(check: CoreMarking | None, questions: QuestionSet) -> tuple[list[str], list[str]]:
    """The questions a marking of questions together marks, and those it only reads, such as a condition's."""
    if (check := runnable(check, questions)) is None:
        return [], []
    marked = check.questions_marked()
    return sorted(marked), sorted(check.questions_read() - marked)


def _group_actions(question: Question, workspace: Workspace | None, groups: list[AnswerGroup]) -> None:
    if not can_mark_by_example(question.core):
        return
    saved = Subject.of(question.assessment, question).marking if workspace is None else None
    marking = workspace.checks.data if workspace else tree_of(saved.definition) if saved else {}
    methods = workspace.methods if workspace else in_use(question.assessment.course)
    decisions = answer_decisions(marking, methods=methods, question=question.core)
    for group in groups:
        decided = {decisions.get(entry.canonical) for entry in group.entries}
        group.decisions = [
            (label, BADGES.get(decision, "badge-ghost")) for decision, label in DECISIONS.items() if decision in decided
        ]
        group.mixed_decisions = bool(group.decisions) and (None in decided or len(group.decisions) > 1)


def _preview_marks(group: AnswerGroup, preview: dict[str, QuestionResult]) -> answer_groups.Preview | None:
    pairs = [(entry, preview[entry.key]) for entry in group.entries if entry.key in preview]
    results = [result for _, result in pairs]
    if not results:
        return None
    projected = [(entry, entry.with_projected(result)) for entry, result in pairs]
    marks = sorted({award.marks for _, award in projected if award.marks is not None})
    unmarked = [result for result in results if not result.marked]
    levels = Counter(award.level for _, award in projected)
    return answer_groups.Preview(
        low=marks[0],
        high=marks[-1],
        feedback=projected[0][1].feedback,
        breakdown=answer_groups.commonest_breakdown(award for _, award in projected),
        full=levels["full"],
        part=levels["part"],
        zero=levels["zero"],
        changes=sum(entry.differs_from(award) for entry, award in projected),
        unmarked=len(unmarked),
        all_unmarked=len(unmarked) == len(results),
        problem=unmarked[0].feedback if unmarked else "",
    )


def _preview_summary(groups: list[AnswerGroup]) -> PreviewSummary:
    previewed = [group.preview for group in groups if group.preview]
    return PreviewSummary(
        changed=sum(shown.changes for shown in previewed),
        unmarked=sum(shown.unmarked for shown in previewed),
        problem=next((shown.problem for shown in previewed if shown.problem), ""),
        full=sum(shown.full for shown in previewed),
        part=sum(shown.part for shown in previewed),
        zero=sum(shown.zero for shown in previewed),
    )


def _across_summary(rows: list[Combination]) -> PreviewSummary:
    unmarked = [row for row in rows if any(cell.problem for cell in row.cells)]
    return PreviewSummary(
        changed=sum(row.changes for row in rows),
        unmarked=sum(row.unmarked for row in unmarked),
        problem=next((cell.problem for row in unmarked for cell in row.cells if cell.problem), ""),
    )


def _matches(group: AnswerGroup, filter_name: str) -> bool:
    levels = group.levels
    match filter_name:
        case "full" | "zero":
            return levels == {filter_name}
        case "part":
            return bool(levels) and levels not in ({"full"}, {"zero"})
        case "blank":
            return group.blank
        case "adjusted":
            return group.adjusted > 0
        case "changed":
            return bool(group.preview and group.preview.changed)
        case "unmarked":
            return bool(group.preview.unmarked if group.preview else group.unmarked)
    return True
