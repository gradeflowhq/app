"""The hero: an assessment from its import to Canvas, which components/landing/hero.js plays."""

from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean
from typing import Any, Literal, cast

from django import forms
from django.utils import timezone

from gradeflow_app.accounts.models import User
from gradeflow_app.editor.editing import EditorActions
from gradeflow_app.editor.picker import PickerForm
from gradeflow_app.editor.presenters import Editor
from gradeflow_app.exports.models import MarksMode
from gradeflow_app.exports.views import Sending
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.landing.content import (
    CoreEditingStage,
    EditingStage,
    Hero,
    HeroAnswer,
    MethodChoice,
    core_output,
    landing_content,
)
from gradeflow_app.landing.demos import (
    ASSESSMENT_ID,
    AnswersColumn,
    largest_first,
    marking_editor,
    student_answers,
    unsaved_question,
)
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer
from gradeflow_app.marking.presenters import ListedQuestion, MarkingSummary, Save, marking_badge
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import Level, level_of
from gradeflow_app.results.models import Adjustment
from gradeflow_app.ui.schemaforms.fields import ListField
from gradeflow_app.ui.schemaforms.forms import ObjectForm

CHAPTERS = {
    "Import": "arrow-up-tray",
    "Mark scheme": "pencil-square",
    "Marking": "check-circle",
    "Adjust marks": "pencil",
    "Students": "users",
    "Send to Canvas": "paper-airplane",
}
"""The chapters the hero plays, with their icons."""
SHOWN_GROUP_COUNT = 6
"""How many answer groups the mockup has room for."""
STARTING_RUN = RunState("running", progress=0.01)
"""A marking run as it starts: the animation fills its progress."""


class StaticTagInput(forms.SelectMultiple):
    """A list's values as Tom Select shows them, without Tom Select: the hero types into it."""

    template_name = "landing/hero/tags.html"


type Action = Literal["add_tag", "select", "type", "add_row", "add_step", "close_step"]


@dataclass(frozen=True)
class Edit:
    """What leads from one stage of the mark scheme to the next, as the animation plays it."""

    action: Action
    field_name: str = ""
    value: str = ""
    """What's typed or chosen; for a step added, its method's label."""
    choices: tuple[tuple[str, str], ...] = ()
    """The options of the list it chooses from, which the animation opens."""


@dataclass(frozen=True)
class EditorStageView:
    editor: Editor
    edit: Edit | None
    """What leads to the next stage; None for the last."""


@dataclass(frozen=True)
class MarksCell:
    marks: float
    level: Level
    adjusted: bool


@dataclass(frozen=True)
class StudentRow:
    student_id: str
    total: float
    cells: list[MarksCell]

    @property
    def adjusted(self) -> bool:
        return any(cell.adjusted for cell in self.cells)


@dataclass(frozen=True)
class HeroView:
    content: Hero
    question: Question
    student_count: int
    total_max_marks: float
    question_lists: list[list[ListedQuestion]]
    """Not marked, Q1 being marked, everything marked, and Q1 adjusted."""
    header_columns: list[AnswersColumn]
    """The answers column's head, then while marking."""
    answer_group_lists: list[list[AnswerGroup]]
    """Not marked, marked, and one group adjusted."""
    adjusted_group_index: int
    everyday: list[MethodChoice]
    advanced_choices: list[MethodChoice]
    step_choices: list[MethodChoice]
    picker_title: str
    editor_stages: list[EditorStageView]
    summary: MarkingSummary
    students: list[StudentRow]
    sending_summary: str
    """How the marks go to Canvas."""

    chapters = CHAPTERS

    @property
    def adjusted_count(self) -> int:
        return self.answer_group_lists[-1][self.adjusted_group_index].adjusted_count

    @property
    def hidden_count(self) -> int:
        """How many answer groups the mockup has no room for."""
        return len(self.content.answers) - SHOWN_GROUP_COUNT

    @property
    def marker(self) -> User:
        return User(name=self.content.marker_name)

    @property
    def last_save(self) -> Save:
        """The marker saving the marking just now."""
        return Save(self.marker, timezone.now())


def stage_editor(content: CoreEditingStage, path: str | None, shown_fields: set[str], id_prefix: str) -> Editor:
    """The editor at a stage: the marking, or its step at `path` with only `shown_fields`."""
    if content.open_step is None or path is None:
        return marking_editor(content.marking, id_prefix)
    step = content.open_step
    properties = {name: field for name, field in step.form_schema["properties"].items() if name in shown_fields}
    form = ObjectForm({**step.form_schema, "properties": properties}, step.values, auto_id=f"{id_prefix}-%s")
    for field in form.fields.values():
        if isinstance(field, ListField):
            field.widget = StaticTagInput()
    [steps] = content.marking.steps
    [card] = [card for card in steps.cards if card.path == path]
    return Editor(
        actions=EditorActions(update="", methods=""),
        path=path,
        form=form,
        label=step.label,
        description=step.description,
        breadcrumbs=[("", content.marking.label), (path, step.label)],
        parent_path="",
        siblings=[(steps.setting.title, steps.cards)],
        can_change_method=True,
        needs_model_endpoint=card.needs_model_endpoint,
        editable=True,
    )


def edit_between(
    before: EditingStage, after: EditingStage, before_content: CoreEditingStage, after_content: CoreEditingStage
) -> Edit:
    """The one change from the stage `before` to the stage `after`, with what the editor shows of each."""
    if before.open_step_path != after.open_step_path:
        return Edit("add_step", value=after_content.open_step.label) if after_content.open_step else Edit("close_step")
    old, new = before_content.open_step or before_content.marking, after_content.open_step or after_content.marking
    form = ObjectForm(old.form_schema, {})
    [(name, value)] = [(name, value) for name, value in new.values.items() if old.values.get(name) != value]
    if name in form.layout.rows:
        rows = old.values.get(name, [])
        if len(value) > len(rows):
            return Edit("add_row", name)
        [(index, key, text)] = [
            (index, key, text)
            for index, (old_row, new_row) in enumerate(zip(rows, value, strict=True))
            for key, text in new_row.items()
            if old_row.get(key) != text
        ]
        return Edit("type", f"{name}-{index}-{key}", str(text))
    field = form.fields[name]
    if isinstance(field, ListField):
        return Edit("add_tag", name, value[-1])
    if isinstance(field, forms.ChoiceField):
        choices = [(str(choice), str(label)) for choice, label in cast(list[tuple[Any, str]], field.choices)]
        return Edit("select", name, str(value), tuple(choices) if isinstance(field.widget, forms.Select) else ())
    return Edit("type", name, str(field.widget.format_value(value)))


def editor_stages(stages: list[EditingStage], contents: list[CoreEditingStage]) -> list[EditorStageView]:
    """Each stage of the mark scheme in the editor, a step showing only the fields it changes."""
    shown_fields: dict[str | None, set[str]] = {}
    for stage, content in zip(stages, contents, strict=True):
        if content.open_step is not None:
            shown_fields.setdefault(stage.open_step_path, set()).update(content.open_step.values)
    edits = [
        edit_between(*pair, *content_pair)
        for pair, content_pair in zip(pairwise(stages), pairwise(contents), strict=True)
    ]
    return [
        EditorStageView(
            stage_editor(content, stage.open_step_path, shown_fields.get(stage.open_step_path, set()), f"hero-{index}"),
            edit,
        )
        for index, (stage, content, edit) in enumerate(zip(stages, contents, [*edits, None], strict=True))
    ]


def hero_view() -> HeroView:
    content, core = landing_content().hero, core_output().hero
    question = unsaved_question(content.question_set, content.question_key)
    max_marks = question.core_question.max_marks
    adjustment = Adjustment(marks=content.adjustment.marks, feedback=content.adjustment.feedback)

    def student_answers_for(answer: HeroAnswer, *, marked: bool = True, adjusted: bool = False) -> list[StudentAnswer]:
        is_adjusted = adjusted and answer.text == content.adjustment.answer
        result = core.results[answer.text] if marked else None
        normalized = core.normalized_answers[answer.text]
        return student_answers(max_marks, answer, normalized, result, adjustment if is_adjusted else None)

    def answer_groups(*, marked: bool = True, adjusted: bool = False) -> list[AnswerGroup]:
        """A group for each answer, as many as the mockup shows."""
        groups = [
            AnswerGroup(student_answers_for(answer, marked=marked, adjusted=adjusted)) for answer in content.answers
        ]
        return largest_first(groups)[:SHOWN_GROUP_COUNT]

    def average_proportion(*, adjusted: bool) -> float:
        return (
            fmean(
                entry.marks or 0
                for answer in content.answers
                for entry in student_answers_for(answer, adjusted=adjusted)
            )
            / max_marks
        )

    unmarked = answer_groups(marked=False)
    adjusted_groups = answer_groups(adjusted=True)
    [adjusted_group_index] = [index for index, group in enumerate(adjusted_groups) if group.adjusted_count]

    labels = core.method_labels | {content.question_key: core.summary.method_label}
    averages = {other.question_key: other.average_percent / 100 for other in content.other_questions}

    def question_list(progress: float | None = None, proportion: float | None = None) -> list[ListedQuestion]:
        """The questions not marked, Q1's marking at `progress`; or all marked, Q1's average `proportion`."""
        is_marked = proportion is not None
        proportions = averages | {content.question_key: proportion}
        return [
            ListedQuestion(
                question=Question(assessment_id=ASSESSMENT_ID, key=key, position=position),
                is_marked=is_marked,
                badge=marking_badge(labels[key] if is_marked else None, has_problems=False, has_imported_marks=False),
                proportion=proportions[key] if is_marked else None,
                unmarked_count=0,
                progress=progress if key == content.question_key else None,
                has_draft=False,
                is_shown=key == content.question_key,
                failure="",
            )
            for position, key in enumerate(content.question_set)
        ]

    def marks_cell(key: str, marks: float, *, adjusted: bool = False) -> MarksCell:
        return MarksCell(marks, level_of(marks, marks, content.question_set[key].max_marks), adjusted)

    students = []
    for student in content.students:
        is_adjusted = student.answer == content.adjustment.answer
        marked_question_marks = content.adjustment.marks if is_adjusted else core.results[student.answer].marks
        cells = [marks_cell(content.question_key, marked_question_marks, adjusted=is_adjusted)]
        cells += [marks_cell(key, marks) for key, marks in student.marks.items()]
        students.append(StudentRow(student.student_id, sum(cell.marks for cell in cells), cells))

    return HeroView(
        content=content,
        question=question,
        student_count=sum(answer.student_count for answer in content.answers),
        total_max_marks=sum(core_question.max_marks for core_question in content.question_set.values()),
        question_lists=[
            question_list(),
            question_list(progress=STARTING_RUN.progress),
            question_list(proportion=average_proportion(adjusted=False)),
            question_list(proportion=average_proportion(adjusted=True)),
        ],
        header_columns=[
            AnswersColumn(question, "answers", unmarked),
            AnswersColumn(question, "answers", unmarked, marking_job=STARTING_RUN),
        ],
        answer_group_lists=[unmarked, answer_groups(), adjusted_groups],
        adjusted_group_index=adjusted_group_index,
        everyday=core.everyday,
        advanced_choices=core.advanced_choices,
        step_choices=core.step_choices,
        picker_title=PickerForm(action="").title,
        editor_stages=editor_stages(content.editing_stages, core.editing_stages),
        summary=core.summary,
        students=students,
        sending_summary=Sending(MarksMode.MARKS, None, include_feedback=True, override_count=0).summary,
    )
