"""The landing page's demos, shown with the app's own question list, answer groups, editor and marking summaries."""

import json
import uuid
from collections.abc import ItemsView
from dataclasses import dataclass
from functools import cache
from typing import Any, ClassVar

from django.utils.text import get_text_list
from gradeflow_core import QuestionResult, QuestionSet
from gradeflow_core.editing import DECISIONS, Decision

from gradeflow_app.editor.editing import EditorActions
from gradeflow_app.editor.presenters import Editor, StepsSettingView
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.landing.content import Answer, EditorContent, Example, core_output, landing_content
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, GroupingOptions, StudentAnswer
from gradeflow_app.marking.answers.column import (
    FILTERS,
    GROUPINGS,
    PreviewSummary,
    preview_marks,
    preview_summary,
    set_decisions,
)
from gradeflow_app.marking.presenters import ListedQuestion, MarkingSummary, QuestionGroup, marking_badge
from gradeflow_app.questions.models import Question
from gradeflow_app.results.models import Adjustment, Result
from gradeflow_app.ui.schemaforms.forms import ObjectForm

GITHUB_URL = "https://github.com/gradeflowhq"
SECTIONS = {"methods": "Methods", "review": "Review", "features": "Features"}
FEATURES = [
    (
        "arrow-up-tray",
        "Import from any platform",
        "ExamSoft and Examplify exams, Canvas, Moodle and Blackboard quizzes, CSV or Excel files, and zips of "
        "students' files.",
    ),
    (
        "paper-airplane",
        "Send marks to Canvas",
        "Students are matched by login, SIS ID or email, with feedback as comments. Or download the marks as a "
        "spreadsheet.",
    ),
    (
        "lock-closed",
        "Encrypted by default",
        "Student IDs, uploaded files and your Canvas and AI tokens are encrypted before they're stored. Only your "
        "course's members can read the IDs.",
    ),
    (
        "user-group",
        "Mark together",
        "Invite colleagues as owners, editors or viewers. Everyone sees marks change as they happen.",
    ),
    ("clock", "History", "Every marking keeps its history. Restore an earlier version at any time."),
    ("puzzle-piece", "Method library", "Save a marking as a method to use in other courses, or make it public."),
]
"""Each feature's icon, title and description."""
ASSESSMENT_ID = uuid.UUID(int=1)
ACCESS = {"can_edit": True, "assessment": {"pk": ASSESSMENT_ID}}
"""The visitor's access: they can edit."""
DRAFT = {"assessment_id": ASSESSMENT_ID, "pk": uuid.UUID(int=2)}
"""The draft the preview bar links to while a marking is edited."""
RUNNING = RunState("running")


@dataclass(frozen=True)
class AnswersColumn:
    """An answers column, as the app's templates read it."""

    question: Question
    grouping: str
    answer_groups: list[AnswerGroup]
    open_answer: str = ""
    """The answer, as the question reads it, whose group is open."""
    filter: str = "all"
    limit: int = 4
    draft: dict[str, uuid.UUID] | None = None
    needs_preview: bool = False
    preview_job: RunState | None = None
    preview_summary: PreviewSummary | None = None
    marking_job: RunState | None = None
    shows_answers: bool = False
    """Whether the open group lists its answers."""
    adjustment: Adjustment | None = None
    """What the open group's Adjust marks form is filled in with."""

    groupings: ClassVar[dict[str, str]] = {name: label for name, (label, _) in GROUPINGS.items()}
    decisions: ClassVar[ItemsView[Decision, str]] = DECISIONS.items()

    @property
    def grouping_options(self) -> GroupingOptions:
        return GROUPINGS[self.grouping][1]

    @property
    def filters(self) -> dict[str, str]:
        """The filters the app offers: "Changed" only while previewing."""
        return {name: label for name, (label, _) in FILTERS.items() if name != "changed" or self.draft}

    @property
    def groups_with_open(self) -> list[tuple[AnswerGroup, bool]]:
        """The groups the filter shows, each with whether it is open."""
        shows = FILTERS[self.filter][1]
        groups = [(group, group.answer == self.open_answer) for group in self.answer_groups if shows(group)]
        return groups[: self.limit]


@dataclass(frozen=True)
class MarkingPanel:
    """The marking beside the answers: its summary, or the editor."""

    summary: MarkingSummary | None = None
    editor: Editor | None = None


@dataclass(frozen=True)
class SceneItem[T]:
    """What a scene shows in the frames listed."""

    frames: tuple[int, ...]
    content: T

    @property
    def frames_attribute(self) -> str:
        return " ".join(map(str, self.frames))


@dataclass(frozen=True)
class Scene:
    """Frames played in a loop: how long each shows, in milliseconds, and the control each presses before the next,
    by selector."""

    durations: tuple[int, ...]
    presses: dict[int, str]

    @property
    def durations_attribute(self) -> str:
        return ",".join(map(str, self.durations))

    @property
    def presses_attribute(self) -> str:
        return json.dumps(self.presses)


def unsaved_question(question_set: QuestionSet, key: str) -> Question:
    return Question(
        assessment_id=ASSESSMENT_ID, key=key, position=0, definition=question_set[key].model_dump(mode="json")
    )


def student_answers(
    max_marks: float, answer: Answer, normalized: str, result: QuestionResult, adjustment: Adjustment | None = None
) -> list[StudentAnswer]:
    """A student answer for each student who gave `answer`."""
    return [
        StudentAnswer(
            max_marks=max_marks,
            result=Result.from_question_result(result, revision=1),
            adjustment=adjustment,
            student_pk=str(uuid.uuid4()),
            encrypted_student_id=f"enc:v1:{uuid.uuid4().hex}",
            answer=answer.text,
            normalized=normalized,
        )
        for _ in range(answer.student_count)
    ]


def set_previews(groups: list[AnswerGroup], results: dict[str, QuestionResult]) -> None:
    """Gives each group the preview of `results`, by encrypted student ID."""
    for group in groups:
        group.preview = preview_marks(group, results)


def marking_editor(content: EditorContent, ids: str) -> Editor:
    """The editor showing `content`, its field IDs starting with `ids`."""
    return Editor(
        actions=EditorActions(update="", methods=""),
        path="",
        form=ObjectForm(content.form_schema, content.values, auto_id=f"{ids}-%s"),
        label=content.label,
        description=content.description,
        settings_holding_steps=[
            StepsSettingView(steps.setting, errors=[], children=steps.cards, questions=[], can_add=True)
            for steps in content.steps
        ],
        can_change_method=True,
        editable=True,
    )


# The hero: question Q5 in the marking workspace, its marking edited, previewed and saved.


@dataclass(frozen=True)
class Hero:
    course: str
    assessment: str
    scene: Scene
    question_lists: list[SceneItem[list[ListedQuestion]]]
    question_groups: list[QuestionGroup]
    answer_columns: list[SceneItem[AnswersColumn]]
    marking_panels: list[SceneItem[MarkingPanel]]

    @property
    def marked_count(self) -> int:
        return sum(1 for question in self.question_lists[0].content if question.is_marked)


HERO_SCENE = Scene(
    durations=(2600, 2000, 2000, 1400, 3400, 2200, 3200),
    presses={
        0: "[data-edit-marking]",
        1: '[aria-label="Remove Docstring"]',
        2: "#preview-bar button",
        4: "footer .btn-primary",
    },
)
"""Saved, editor open, step removed, previewing, previewed, marking, marked."""
STARTING_PROGRESS = 0.05
"""Q5's marking progress as it starts."""


def average(groups: list[AnswerGroup]) -> float:
    """The average proportion of the marks of every student in `groups`."""
    entries = [entry for group in groups for entry in group.student_answers]
    return sum(entry.marks or 0 for entry in entries) / sum(entry.max_marks for entry in entries)


@cache
def hero() -> Hero:
    workspace, core = landing_content().workspace, core_output().workspace
    question = unsaved_question(workspace.question_set, workspace.question_key)
    max_marks = question.core_question.max_marks
    answers = [answer for group in workspace.answer_groups for answer in group]
    normalized = [text for group in core.normalized for text in group]

    def by_feedback(results: list[QuestionResult]) -> list[AnswerGroup]:
        """The answers grouped by their feedback in `results`, the commonest first."""
        groups: dict[str, list[StudentAnswer]] = {}
        for answer, text, result in zip(answers, normalized, results, strict=True):
            groups.setdefault(result.feedback, []).extend(student_answers(max_marks, answer, text, result))
        return sorted(map(AnswerGroup, groups.values()), key=lambda group: -group.student_count)

    def question_list(progress: float | None = None, proportion: float | None = None) -> list[ListedQuestion]:
        """The questions, Q5 with its marking `progress` and average `proportion`."""
        listed = []
        for position, item in enumerate(workspace.questions):
            is_shown = item.question_key == workspace.question_key
            label = "Question group" if item.in_question_group else item.method and core.method_labels[item.method]
            listed.append(
                ListedQuestion(
                    question=Question(assessment_id=ASSESSMENT_ID, key=item.question_key, position=position),
                    is_marked=bool(item.method or item.in_question_group),
                    badge=marking_badge(label, has_problems=False, has_imported_marks=False),
                    proportion=proportion if is_shown else None if item.average is None else item.average / 100,
                    progress=progress if is_shown else None,
                    has_draft=False,
                    is_shown=is_shown,
                )
            )
        return listed

    results = [result for group in core.results for result in group]
    saved = by_feedback(results)
    previewed = by_feedback(results)
    edited_results = dict(zip((answer.text for answer in answers), core.edited_results, strict=True))
    set_previews(
        previewed,
        {
            entry.encrypted_student_id: edited_results[entry.answer]
            for group in previewed
            for entry in group.student_answers
        },
    )
    marked = by_feedback(core.edited_results)

    return Hero(
        workspace.course,
        workspace.assessment,
        HERO_SCENE,
        [
            SceneItem((0, 1, 2, 3, 4), question_list(proportion=average(saved))),
            SceneItem((5,), question_list(progress=STARTING_PROGRESS, proportion=average(saved))),
            SceneItem((6,), question_list(proportion=average(marked))),
        ],
        [
            QuestionGroup("#", group.questions, core.method_labels[group.method], has_draft=False, is_shown=False)
            for group in workspace.question_groups
        ],
        [
            SceneItem((0, 1), AnswersColumn(question, "feedback", saved)),
            SceneItem((2,), AnswersColumn(question, "feedback", saved, draft=DRAFT, needs_preview=True)),
            SceneItem((3,), AnswersColumn(question, "feedback", saved, draft=DRAFT, preview_job=RUNNING)),
            SceneItem(
                (4,),
                AnswersColumn(question, "feedback", previewed, draft=DRAFT, preview_summary=preview_summary(previewed)),
            ),
            SceneItem((5,), AnswersColumn(question, "feedback", saved, marking_job=RUNNING)),
            SceneItem((6,), AnswersColumn(question, "feedback", marked)),
        ],
        [
            SceneItem((0,), MarkingPanel(summary=core.summary)),
            SceneItem((1,), MarkingPanel(editor=marking_editor(core.editor, "hero"))),
            SceneItem((2, 3, 4), MarkingPanel(editor=marking_editor(core.edited_editor, "hero-edited"))),
            SceneItem((5, 6), MarkingPanel(summary=core.edited_summary)),
        ],
    )


# The review section: Q1's answers grouped by meaning, marked by example, then adjusted.


@dataclass(frozen=True)
class ReviewStep:
    title: str
    description: str


@dataclass(frozen=True)
class Review:
    scene: Scene
    steps: list[SceneItem[ReviewStep]]
    answer_columns: list[SceneItem[AnswersColumn]]
    marking_panels: list[SceneItem[MarkingPanel]]


REVIEW_SCENE = Scene(
    durations=(2000, 3400, 2200, 3400, 1800, 2400, 3200),
    presses={
        0: "label.select:has([name=group])",
        2: '[name=decision][value="full_marks"]',
        4: "label:has(> [data-adjust])",
        5: '[name=action][value="apply"]',
    },
)
"""Same answers, similar meaning, marking by example, marked, adjusting, form filled in, adjusted."""
REVIEW_STEPS = [
    SceneItem(
        (0, 1),
        ReviewStep("Group answers", "By the same answer, similar spelling, similar meaning or the same feedback."),
    ),
    SceneItem(
        (2, 3),
        ReviewStep(
            "Mark by example", "Give an answer group full, partial or no marks. The preview shows whose marks change."
        ),
    ),
    SceneItem(
        (4, 5, 6),
        ReviewStep(
            "Adjust marks", "Set marks and feedback for a group's students by hand. The marking keeps the rest."
        ),
    ),
]


@cache
def review() -> Review:
    content, core = landing_content().review, core_output().review
    question = unsaved_question(content.question_set, content.question_key)
    max_marks = question.core_question.max_marks
    normalized = dict(zip((answer.text for answer in content.answers), core.normalized, strict=True))
    adjustment = Adjustment(marks=content.adjustment.marks, feedback=content.adjustment.feedback)

    def by_answer(results: list[QuestionResult], *, decided: bool = False, adjusted: bool = False) -> list[AnswerGroup]:
        """A group for each answer, with `results`, the decisions if `decided`, and the adjustment if `adjusted`."""
        groups = [
            AnswerGroup(
                student_answers(
                    max_marks,
                    answer,
                    normalized[answer.text],
                    result,
                    adjustment if adjusted and answer.text == content.adjustment.answer else None,
                )
            )
            for answer, result in zip(content.answers, results, strict=True)
        ]
        set_decisions(groups, core.decisions if decided else {})
        return groups

    by_meaning: dict[str, list[StudentAnswer]] = {}
    for answer, result in zip(content.answers, core.results, strict=True):
        by_meaning.setdefault(answer.meaning_group, []).extend(
            student_answers(max_marks, answer, normalized[answer.text], result)
        )
    meaning_groups = [
        AnswerGroup(entries, joined_by_similarity=len({entry.normalized for entry in entries}) > 1)
        for entries in by_meaning.values()
    ]
    previewed = by_answer(core.results, decided=True)
    set_previews(
        previewed,
        {
            entry.encrypted_student_id: result
            for group, result in zip(previewed, core.decided_results, strict=True)
            for entry in group.student_answers
        },
    )
    decided_answer, adjusted_answer = normalized[content.decided_answer], normalized[content.adjustment.answer]
    decided = by_answer(core.decided_results, decided=True)

    return Review(
        REVIEW_SCENE,
        REVIEW_STEPS,
        [
            SceneItem((0,), AnswersColumn(question, "answers", by_answer(core.results))),
            SceneItem((1,), AnswersColumn(question, "meaning", meaning_groups, decided_answer, shows_answers=True)),
            SceneItem((2,), AnswersColumn(question, "answers", by_answer(core.results), decided_answer, draft=DRAFT)),
            SceneItem(
                (3,),
                AnswersColumn(
                    question,
                    "answers",
                    previewed,
                    decided_answer,
                    draft=DRAFT,
                    preview_summary=preview_summary(previewed),
                ),
            ),
            SceneItem((4,), AnswersColumn(question, "answers", decided, adjusted_answer, "no_marks")),
            SceneItem(
                (5,), AnswersColumn(question, "answers", decided, adjusted_answer, "no_marks", adjustment=adjustment)
            ),
            SceneItem(
                (6,),
                AnswersColumn(
                    question,
                    "answers",
                    by_answer(core.decided_results, decided=True, adjusted=True),
                    adjusted_answer,
                    "adjusted",
                ),
            ),
        ],
        [
            SceneItem((0, 1), MarkingPanel(summary=core.summary)),
            SceneItem((2,), MarkingPanel(editor=marking_editor(core.editor, "review"))),
            SceneItem((3,), MarkingPanel(editor=marking_editor(core.decided_editor, "review-decided"))),
            SceneItem((4, 5, 6), MarkingPanel(summary=core.decided_summary)),
        ],
    )


# The methods: an example of each, its settings changed one step at a time, with its answers' marks.


@dataclass(frozen=True)
class AnswerRow:
    answers: dict[str, str]
    """Each answer, by question; the one marked comes last."""
    student_count: int
    result: QuestionResult
    previous_marks: float | None
    """Its marks before the change; None for the first variant."""

    @property
    def answer(self) -> str:
        return list(self.answers.values())[-1]

    @property
    def marks(self) -> float | None:
        return self.result.marks if self.result.is_marked else None

    @property
    def marks_changed(self) -> bool:
        return self.previous_marks is not None and self.previous_marks != self.marks


@dataclass(frozen=True)
class VariantView:
    change: str
    description: str
    rows: list[AnswerRow]


@dataclass(frozen=True)
class ExampleView:
    key: str
    example: Example
    method_label: str
    method_summary: str
    variants: list[VariantView]


@cache
def examples() -> list[ExampleView]:
    """Every example with each variant of its settings, its answers' marks beside those before the change."""
    views = []
    for key, example in landing_content().examples.items():
        core = core_output().examples[key]
        variants = []
        previous: list[AnswerRow] = []
        for variant, core_variant in zip(example.variants, core.variants, strict=True):
            rows = [
                AnswerRow(answer.answers, answer.student_count, result, previous[index].marks if previous else None)
                for index, (answer, result) in enumerate(zip(example.answers, core_variant.results, strict=True))
            ]
            variants.append(VariantView(variant.change, core_variant.description, rows))
            previous = rows
        views.append(ExampleView(key, example, core.method_label, core.method_summary, variants))
    return views


def landing_context() -> dict[str, Any]:
    """What the landing page shows, apart from the choice of method example."""
    return {
        "github_url": GITHUB_URL,
        "sections": SECTIONS,
        "access": ACCESS,
        "hero": hero(),
        "review": review(),
        "features": FEATURES,
        "examples": examples(),
        "other_methods": get_text_list(core_output().other_methods, "and"),
    }
